"""Test doubles shared by the test suites."""

from collections.abc import Sequence

from openai.types.chat import ChatCompletionMessageParam

from diff_analyzer.llm_client import LLMResponse


class FakeLLMClient:
    """`LLMClient` that returns or raises the given outcomes in order and records each call."""

    def __init__(self, *outcomes: LLMResponse | Exception) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[list[ChatCompletionMessageParam]] = []

    def complete(self, messages: Sequence[ChatCompletionMessageParam]) -> LLMResponse:
        self.calls.append(list(messages))
        if not self._outcomes:
            raise AssertionError("FakeLLMClient was called more times than it has outcomes")
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome
