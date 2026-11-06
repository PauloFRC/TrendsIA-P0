"""Runs the flow: read diff, validate, build prompt, call the LLM."""

from dataclasses import dataclass, field

from diff_analyzer.diff_source import GitMode, WorkingTree, read_diff
from diff_analyzer.errors import InputError
from diff_analyzer.llm_client import LLMClient, LLMResponse
from diff_analyzer.prompt import DefaultPrompt, PromptSource, build_messages, resolve_prompt


@dataclass(frozen=True, slots=True)
class AnalysisRequest:
    source: str
    mode: GitMode = field(default_factory=WorkingTree)
    prompt: PromptSource = field(default_factory=DefaultPrompt)


def analyze(request: AnalysisRequest, client: LLMClient) -> LLMResponse:
    """Send the diff and prompt of `request` to `client` and return its answer."""
    diff = read_diff(request.source, request.mode)
    if not diff.strip():
        raise InputError("O diff está vazio; não há nada para analisar.")
    prompt = resolve_prompt(request.prompt)
    return client.complete(build_messages(prompt, diff))
