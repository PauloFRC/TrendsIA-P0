"""Tests for the command line: arguments, stdout/stderr split and exit codes."""

import os
import shutil
import subprocess
from pathlib import Path

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
from diff_analyzer.llm_client import LLMResponse
from diff_analyzer.prompt import DefaultPrompt, resolve_prompt
from fakes import FakeLLMClient

GIT = shutil.which("git") or "git"
FILE_DIFF = "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n"


@pytest.fixture(autouse=True)
def in_tmp_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # No .env, so settings fall back to the code defaults.
    monkeypatch.chdir(tmp_path)


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
    "argv",
    [
        [],
        ["--staged", "--commit", "HEAD", "."],
        ["--commit", "HEAD", "--range", "a..b", "."],
        ["-p", "Resuma.", "--prompt-file", "prompt.md", "."],
        ["--unknown", "."],
    ],
    ids=["no-source", "staged-and-commit", "commit-and-range", "both-prompts", "unknown-flag"],
)
def test_usage_error_exits_2_with_only_an_error_line_on_stderr(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    client = FakeLLMClient()

    code = run(argv, client)

    out, err = capsys.readouterr()
    assert code == 2
    assert out == ""
    assert err.startswith("Erro: ")
    assert len(err.splitlines()) == 1
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
