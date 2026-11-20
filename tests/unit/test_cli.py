"""Tests for the command line: arguments, stdout/stderr split and exit codes."""

import os
import shutil
import subprocess
from pathlib import Path

import httpx2
import openai
import pytest

from diff_analyzer.cli import main
from diff_analyzer.config import Settings
from diff_analyzer.errors import (
    ConfigurationError,
    DiffAnalyzerError,
    InputError,
    LLMAuthenticationError,
    LLMResponseError,
    LLMUnreachableError,
)
from diff_analyzer.llm_client import LLMResponse, OpenAIClient
from diff_analyzer.prompt import DefaultPrompt, resolve_prompt
from fakes import FakeLLMClient

GIT = shutil.which("git") or "git"
FILE_DIFF = "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n"
ENV_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"


@pytest.fixture(autouse=True)
def in_tmp_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # LLM_MODEL has no default.
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("LLM_MODEL=modelo:teste\n", encoding="utf-8")


@pytest.fixture
def diff_file(tmp_path: Path) -> Path:
    path = tmp_path / "change.diff"
    path.write_text(FILE_DIFF, encoding="utf-8")
    return path


def run(argv: list[str], client: FakeLLMClient) -> int:
    def factory(settings: Settings) -> FakeLLMClient:
        return client

    return main(argv, client_factory=factory)


def system_prompt(client: FakeLLMClient) -> object:
    return client.calls[0][0]["content"]


def user_message(client: FakeLLMClient) -> object:
    return client.calls[0][1]["content"]


def test_help_documents_every_argument(capsys: pytest.CaptureFixture[str]) -> None:
    code = run(["--help"], FakeLLMClient())

    out, err = capsys.readouterr()
    assert code == 0
    assert err == ""
    assert out.startswith("uso: diff-analyzer ")
    for argument in [
        "SOURCE",
        "--staged",
        "--commit",
        "--range",
        "-p",
        "--prompt",
        "--prompt-file",
    ]:
        assert argument in out


def test_answer_goes_to_stdout_with_a_final_newline(
    diff_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    client = FakeLLMClient(LLMResponse("Troca a por b.", "stop"))

    code = run([str(diff_file)], client)

    assert code == 0
    assert capsys.readouterr() == ("Troca a por b.\n", "")
    assert user_message(client) == f"<diff>\n{FILE_DIFF}</diff>"


def test_disabled_tls_verification_warns_on_stderr_before_building_the_client(
    diff_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    Path(".env").write_text("LLM_MODEL=modelo:teste\nLLM_TLS_VERIFY=false\n", encoding="utf-8")

    def factory(settings: Settings) -> FakeLLMClient:
        out, err = capsys.readouterr()
        assert out == ""
        assert "TLS" in err
        return FakeLLMClient(LLMResponse("Troca a por b.", "stop"))

    code = main([str(diff_file)], client_factory=factory)

    assert code == 0


def test_answer_ending_in_newline_gets_no_extra_one(
    diff_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = run([str(diff_file)], FakeLLMClient(LLMResponse("Ok.\n", "stop")))

    assert code == 0
    assert capsys.readouterr().out == "Ok.\n"


def test_without_prompt_flags_the_default_prompt_is_sent(diff_file: Path) -> None:
    client = FakeLLMClient(LLMResponse("Ok.", "stop"))

    run([str(diff_file)], client)

    assert system_prompt(client) == resolve_prompt(DefaultPrompt())


@pytest.mark.parametrize("flag", ["-p", "--prompt"])
def test_inline_prompt_is_sent_as_the_system_message(diff_file: Path, flag: str) -> None:
    client = FakeLLMClient(LLMResponse("Ok.", "stop"))

    run([flag, "Resuma em uma linha.", str(diff_file)], client)

    assert system_prompt(client) == "Resuma em uma linha."


def test_prompt_file_is_sent_as_the_system_message(diff_file: Path, tmp_path: Path) -> None:
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("Liste os riscos.\n", encoding="utf-8")
    client = FakeLLMClient(LLMResponse("Ok.", "stop"))

    run(["--prompt-file", str(prompt_file), str(diff_file)], client)

    assert system_prompt(client) == "Liste os riscos."


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Repository where each git mode sees a different change of a.txt."""
    for name in [name for name in os.environ if name.startswith("GIT_")]:
        monkeypatch.delenv(name)
    monkeypatch.setenv("LC_ALL", "C")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-global-gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    root = tmp_path / "repo"
    root.mkdir()

    def git(*args: str) -> None:
        identity = ["-c", "user.name=Test", "-c", "user.email=test@example.com"]
        subprocess.run([GIT, "-C", str(root), *identity, *args], check=True)  # noqa: S603

    def change(content: str, *commands: list[str]) -> None:
        (root / "a.txt").write_text(f"{content}\n", encoding="utf-8")
        for command in commands:
            git(*command)

    git("init", "-q")
    change("1", ["add", "a.txt"], ["commit", "-q", "-m", "c1"])
    change("2", ["commit", "-q", "-am", "c2"])
    change("3", ["add", "a.txt"])
    change("4")
    return root


@pytest.mark.parametrize(
    ("flags", "removed", "added"),
    [
        ([], "-2", "+4"),
        (["--staged"], "-2", "+3"),
        (["--commit", "HEAD"], "-1", "+2"),
        (["--range", "HEAD~1..HEAD"], "-1", "+2"),
    ],
    ids=["working-tree", "staged", "commit", "range"],
)
def test_git_flags_select_the_diff_of_the_repository(
    repo: Path, flags: list[str], removed: str, added: str
) -> None:
    client = FakeLLMClient(LLMResponse("Ok.", "stop"))

    code = run([*flags, str(repo)], client)

    assert code == 0
    lines = str(user_message(client)).splitlines()
    assert removed in lines
    assert added in lines


@pytest.mark.parametrize(
    ("argv", "error"),
    [
        ([], "faltam argumentos obrigatórios: SOURCE"),
        (["--staged", "--commit", "HEAD", "."], "--commit não pode ser usado com --staged"),
        (["--commit", "HEAD", "--range", "a..b", "."], "--range não pode ser usado com --commit"),
        (
            ["-p", "Resuma.", "--prompt-file", "prompt.md", "."],
            "--prompt-file não pode ser usado com -p/--prompt",
        ),
        (["--unknown", "."], "argumentos não reconhecidos: --unknown"),
        ([".", "-p"], "-p/--prompt espera um valor"),
        (["--staged=sim", "."], "--staged não aceita valor, recebido: 'sim'"),
        (["--pr", "x", "."], "opção ambígua: --pr pode ser --prompt, --prompt-file"),
    ],
    ids=[
        "no-source",
        "staged-and-commit",
        "commit-and-range",
        "both-prompts",
        "unknown-flag",
        "missing-value",
        "value-for-a-switch",
        "ambiguous-prefix",
    ],
)
def test_usage_error_exits_2_with_one_error_line(
    argv: list[str], error: str, capsys: pytest.CaptureFixture[str]
) -> None:
    client = FakeLLMClient()

    code = run(argv, client)

    assert code == 2
    assert capsys.readouterr() == ("", f"Erro: {error}\n")
    assert client.calls == []


@pytest.mark.parametrize(
    "error",
    [
        LLMAuthenticationError("Credencial recusada."),
        LLMUnreachableError("LLM fora do ar."),
        LLMResponseError("Resposta inutilizável."),
    ],
    ids=lambda error: type(error).__name__,
)
def test_llm_error_exits_with_its_code_and_writes_only_to_stderr(
    diff_file: Path, error: DiffAnalyzerError, capsys: pytest.CaptureFixture[str]
) -> None:
    code = run([str(diff_file)], FakeLLMClient(error))

    assert code == error.exit_code
    assert capsys.readouterr() == ("", f"Erro: {error}\n")


def test_empty_diff_exits_3_without_calling_the_llm(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    empty = tmp_path / "empty.diff"
    empty.write_bytes(b"")
    client = FakeLLMClient()

    code = run([str(empty)], client)

    out, err = capsys.readouterr()
    assert code == InputError.exit_code
    assert out == ""
    assert err.startswith("Erro: O diff está vazio")
    assert client.calls == []


def test_malformed_range_exits_3(capsys: pytest.CaptureFixture[str]) -> None:
    code = run(["--range", "HEAD", "."], FakeLLMClient())

    out, err = capsys.readouterr()
    assert code == InputError.exit_code
    assert out == ""
    assert err.startswith("Erro: --range")


def test_invalid_env_exits_4_before_the_diff_is_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / ".env").write_text("LLM_AUTH=maybe\n", encoding="utf-8")
    empty = tmp_path / "empty.diff"
    empty.write_bytes(b"")
    built: list[Settings] = []

    def factory(settings: Settings) -> FakeLLMClient:
        built.append(settings)
        return FakeLLMClient()

    code = main([str(empty)], client_factory=factory)

    out, err = capsys.readouterr()
    assert code == ConfigurationError.exit_code
    assert out == ""
    assert err.startswith("Erro: Configuração inválida no .env")
    assert built == []


def env_from_example(*, auth: str) -> None:
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    text = text.replace("LLM_MODEL=\n", "LLM_MODEL=modelo:teste\n")
    text = text.replace("LLM_AUTH=required\n", f"LLM_AUTH={auth}\n")
    Path(".env").write_text(text, encoding="utf-8")


def run_with_real_client(argv: list[str]) -> tuple[int, list[httpx2.Request]]:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        message = {"role": "assistant", "content": "Ok."}
        return httpx2.Response(
            200, json={"choices": [{"index": 0, "message": message, "finish_reason": "stop"}]}
        )

    def factory(settings: Settings) -> OpenAIClient:
        transport = httpx2.MockTransport(handler)
        return OpenAIClient(settings, http_client=openai.DefaultHttpxClient(transport=transport))

    return main(argv, client_factory=factory), requests


def test_env_example_without_a_key_exits_4_before_any_request(
    diff_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_from_example(auth="required")

    code, requests = run_with_real_client([str(diff_file)])

    out, err = capsys.readouterr()
    assert code == ConfigurationError.exit_code
    assert out == ""
    assert err.startswith("Erro: ")
    assert "LLM_API_KEY" in err
    assert requests == []


def test_env_example_with_auth_none_runs_without_a_key(
    diff_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_from_example(auth="none")

    code, requests = run_with_real_client([str(diff_file)])

    assert code == 0
    assert capsys.readouterr() == ("Ok.\n", "")
    assert len(requests) == 1
