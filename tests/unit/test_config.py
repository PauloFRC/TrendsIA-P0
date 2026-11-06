"""Tests for loading settings from `.env` with code defaults."""

import traceback
from pathlib import Path

import pytest

from diff_analyzer.config import load_settings
from diff_analyzer.errors import ConfigurationError

SENTINEL = "sk-SENTINEL-9f3a"


@pytest.fixture(autouse=True)
def in_tmp_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


def write_env(text: str) -> None:
    Path(".env").write_text(text, encoding="utf-8")


def load_error(text: str) -> ConfigurationError:
    write_env(text)
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    return excinfo.value


def test_without_env_file_all_defaults_apply() -> None:
    settings = load_settings()

    assert settings.base_url == "http://localhost:11434/v1"
    assert settings.model == "qwen2.5-coder:7b"
    assert settings.auth == "required"
    assert settings.api_key.get_secret_value() == ""
    assert settings.data_egress == "external"
    assert settings.timeout_connect == 5
    assert settings.timeout_read == 120
    assert settings.temperature == 0
    assert settings.seed == 42
    assert settings.max_input_chars == 60000
    assert settings.max_output_tokens == 1024


def test_env_value_overrides_default_and_missing_key_keeps_default() -> None:
    write_env("LLM_MODEL=llama3.1:8b\nLLM_AUTH=none\nLLM_TIMEOUT_READ=30.5\n")

    settings = load_settings()

    assert settings.model == "llama3.1:8b"
    assert settings.auth == "none"
    assert settings.timeout_read == 30.5
    assert settings.timeout_connect == 5
    assert settings.base_url == "http://localhost:11434/v1"


def test_process_environment_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_MODEL", "from-environ")
    monkeypatch.setenv("LLM_API_KEY", SENTINEL)
    write_env("LLM_AUTH=none\n")

    settings = load_settings()

    assert settings.model == "qwen2.5-coder:7b"
    assert settings.api_key.get_secret_value() == ""


def test_env_file_does_not_interpolate_process_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOST_MODEL", "from-environ")
    write_env("LLM_MODEL=${HOST_MODEL}\n")

    assert load_settings().model == "${HOST_MODEL}"


def test_empty_value_falls_back_to_default() -> None:
    write_env("LLM_MODEL=\nLLM_TIMEOUT_READ=\nLLM_API_KEY=\n")

    settings = load_settings()

    assert settings.model == "qwen2.5-coder:7b"
    assert settings.timeout_read == 120
    assert settings.api_key.get_secret_value() == ""


def test_range_boundaries_are_accepted() -> None:
    write_env(
        "LLM_TIMEOUT_CONNECT=0.1\nLLM_TEMPERATURE=2\nLLM_SEED=0\n"
        "LLM_MAX_INPUT_CHARS=1\nLLM_MAX_OUTPUT_TOKENS=1\n"
    )

    settings = load_settings()

    assert settings.timeout_connect == 0.1
    assert settings.temperature == 2
    assert settings.seed == 0
    assert settings.max_input_chars == 1
    assert settings.max_output_tokens == 1


def test_base_url_is_kept_as_written() -> None:
    write_env("LLM_BASE_URL=https://gpu-box:8000\n")

    assert load_settings().base_url == "https://gpu-box:8000"


@pytest.mark.parametrize(
    "key",
    [
        "LLM_AUTH",
        "LLM_DATA_EGRESS",
        "LLM_BASE_URL",
        "LLM_TIMEOUT_CONNECT",
        "LLM_TIMEOUT_READ",
        "LLM_TEMPERATURE",
        "LLM_SEED",
        "LLM_MAX_INPUT_CHARS",
        "LLM_MAX_OUTPUT_TOKENS",
    ],
)
def test_malformed_value_names_key_without_value(key: str) -> None:
    err = load_error(f"{key}={SENTINEL}\n")

    assert key in str(err)
    assert SENTINEL not in str(err)


@pytest.mark.parametrize(
    "line",
    [
        "LLM_AUTH=NONE",
        "LLM_BASE_URL=ftp://example.com/v1",
        "LLM_TIMEOUT_CONNECT=0",
        "LLM_TIMEOUT_READ=-1",
        "LLM_TEMPERATURE=2.1",
        "LLM_TEMPERATURE=-0.1",
        "LLM_SEED=-1",
        "LLM_MAX_INPUT_CHARS=0",
        "LLM_MAX_OUTPUT_TOKENS=0",
    ],
)
def test_out_of_range_value_is_rejected_naming_key(line: str) -> None:
    key = line.split("=")[0]

    assert key in str(load_error(line + "\n"))


@pytest.mark.parametrize("value", [SENTINEL, ""])
def test_unknown_key_is_rejected_naming_key(value: str) -> None:
    err = load_error(f"LLM_TIMEOUT_REED={value}\nOTHER_TOOL_TOKEN={value}\n")

    assert "LLM_TIMEOUT_REED" in str(err)
    assert "OTHER_TOOL_TOKEN" in str(err)
    assert SENTINEL not in str(err)


@pytest.mark.parametrize("bad_line", [f'LLM_MODEL="{SENTINEL}', f"{SENTINEL} is not a setting"])
def test_unparsable_line_is_rejected_naming_line(bad_line: str) -> None:
    err = load_error(f"LLM_AUTH=none\n\n{bad_line}\nLLM_SEED=7\n")

    assert "linha 3" in str(err)
    assert SENTINEL not in str(err)


def test_every_invalid_key_is_reported() -> None:
    err = load_error("LLM_AUTH=maybe\nLLM_TIMEOUT_READ=abc\n")

    assert "LLM_AUTH" in str(err)
    assert "LLM_TIMEOUT_READ" in str(err)
    assert "maybe" not in str(err)
    assert "abc" not in str(err)


def test_secret_never_leaks_through_configuration_error() -> None:
    err = load_error(f"LLM_API_KEY={SENTINEL}\nLLM_AUTH=maybe\nLLM_SEED={SENTINEL}\n")

    assert SENTINEL not in str(err)
    # What --verbose would print.
    assert SENTINEL not in "".join(traceback.format_exception(err))


def test_api_key_is_kept_and_masked() -> None:
    write_env(f"LLM_API_KEY={SENTINEL}\n")

    settings = load_settings()

    assert SENTINEL not in repr(settings)
    assert SENTINEL not in str(settings)
    assert settings.api_key.get_secret_value() == SENTINEL
