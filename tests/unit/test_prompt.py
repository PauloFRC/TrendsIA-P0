"""Tests for prompt resolution and chat message building."""

from pathlib import Path

import pytest

from diff_analyzer.errors import InputError
from diff_analyzer.prompt import (
    DIFF_BEGIN,
    DIFF_END,
    DefaultPrompt,
    InlinePrompt,
    PromptFile,
    build_messages,
    resolve_prompt,
)

FILE_DIFF = "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n"


def test_default_prompt_is_stripped_and_names_the_diff_delimiters() -> None:
    text = resolve_prompt(DefaultPrompt())

    assert text == text.strip()
    assert DIFF_BEGIN in text
    assert DIFF_END in text


def test_inline_prompt_is_used_stripped() -> None:
    assert resolve_prompt(InlinePrompt("  Resuma o diff.\n")) == "Resuma o diff."


@pytest.mark.parametrize(
    "content",
    [
        b"Resuma o diff.",
        b"\n  Resuma o diff.\n\n",
        b"\xef\xbb\xbfResuma o diff.\n",
    ],
    ids=["plain", "surrounding-whitespace", "utf8-bom"],
)
def test_prompt_file_is_read_stripped(tmp_path: Path, content: bytes) -> None:
    path = tmp_path / "prompt.md"
    path.write_bytes(content)

    assert resolve_prompt(PromptFile(path)) == "Resuma o diff."


def test_prompt_file_keeps_non_ascii_text(tmp_path: Path) -> None:
    path = tmp_path / "prompt.md"
    path.write_text("Análise em português, por favor.", encoding="utf-8")

    assert resolve_prompt(PromptFile(path)) == "Análise em português, por favor."


@pytest.mark.parametrize("text", ["", "  \n\t"], ids=["empty", "whitespace"])
def test_blank_inline_prompt_is_an_input_error(text: str) -> None:
    with pytest.raises(InputError, match="-p/--prompt"):
        resolve_prompt(InlinePrompt(text))


@pytest.mark.parametrize(
    "content",
    [b"", b" \n\n\t", b"\xef\xbb\xbf", b"\xef\xbb\xbf \r\n\t"],
    ids=["empty", "whitespace", "bom-only", "bom-whitespace"],
)
def test_blank_prompt_file_is_an_input_error(tmp_path: Path, content: bytes) -> None:
    path = tmp_path / "prompt.md"
    path.write_bytes(content)

    with pytest.raises(InputError, match="vazio") as excinfo:
        resolve_prompt(PromptFile(path))
    assert str(path) in str(excinfo.value)


def test_missing_prompt_file_is_an_input_error(tmp_path: Path) -> None:
    path = tmp_path / "missing.md"

    with pytest.raises(InputError, match="não encontrado") as excinfo:
        resolve_prompt(PromptFile(path))
    assert str(path) in str(excinfo.value)
    assert excinfo.value.__suppress_context__


def test_unreadable_prompt_file_is_an_input_error(tmp_path: Path) -> None:
    with pytest.raises(InputError, match="Não foi possível ler") as excinfo:
        resolve_prompt(PromptFile(tmp_path))
    assert str(tmp_path) in str(excinfo.value)
    assert excinfo.value.__suppress_context__


def test_non_utf8_prompt_file_is_an_input_error(tmp_path: Path) -> None:
    path = tmp_path / "prompt.md"
    path.write_bytes(b"An\xe1lise")

    with pytest.raises(InputError, match="UTF-8") as excinfo:
        resolve_prompt(PromptFile(path))
    assert str(path) in str(excinfo.value)
    assert excinfo.value.__suppress_context__


def test_messages_send_prompt_as_system_and_delimited_diff_as_user() -> None:
    # Braces stay literal: prompts have no placeholders.
    prompt = "Analise {diff} com cuidado."

    assert build_messages(prompt, FILE_DIFF) == [
        {"role": "system", "content": prompt},
        {"role": "user", "content": f"{DIFF_BEGIN}\n{FILE_DIFF}{DIFF_END}"},
    ]


@pytest.mark.parametrize(
    ("diff", "content"),
    [
        ("-a\r\n+b\r\n", "<diff>\n-a\r\n+b\r\n</diff>"),
        ("+b  \n\n\n", "<diff>\n+b  \n\n\n</diff>"),
        ("+x = '{diff}'\n", "<diff>\n+x = '{diff}'\n</diff>"),
        ("+b", "<diff>\n+b\n</diff>"),
        ("+<diff>\n+</diff>\n", "<diff>\n+<diff>\n+</diff>\n</diff>"),
    ],
    ids=["crlf", "trailing-whitespace", "braces", "no-trailing-newline", "literal-markers"],
)
def test_diff_is_sent_verbatim_between_the_markers(diff: str, content: str) -> None:
    assert build_messages("Analise.", diff)[1] == {"role": "user", "content": content}
