"""Tests for the service flow: diff and prompt in, LLM answer out."""

import io
from pathlib import Path

import pytest

from diff_analyzer.diff_source import STDIN_SOURCE
from diff_analyzer.errors import InputError
from diff_analyzer.llm_client import LLMResponse
from diff_analyzer.prompt import InlinePrompt
from diff_analyzer.service import AnalysisRequest, analyze
from fakes import FakeLLMClient

FILE_DIFF = "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n"


def test_returns_the_llm_answer_for_the_diff_and_prompt(tmp_path: Path) -> None:
    diff_file = tmp_path / "change.diff"
    diff_file.write_text(FILE_DIFF, encoding="utf-8")
    answer = LLMResponse("Troca a por b.", "stop")
    client = FakeLLMClient(answer)

    result = analyze(AnalysisRequest(str(diff_file), prompt=InlinePrompt("Resuma.")), client)

    assert result == answer
    assert client.calls == [
        [
            {"role": "system", "content": "Resuma."},
            {"role": "user", "content": f"<diff>\n{FILE_DIFF}</diff>"},
        ]
    ]


@pytest.mark.parametrize("data", [b"", b"  \n\t\n"], ids=["empty", "whitespace"])
def test_empty_diff_is_an_input_error_without_calling_the_llm(data: bytes) -> None:
    client = FakeLLMClient()

    with pytest.raises(InputError, match="O diff está vazio"):
        analyze(AnalysisRequest(STDIN_SOURCE), client, stdin=io.BytesIO(data))

    assert client.calls == []
