"""Resolves the prompt and builds the chat messages sent to the LLM."""

from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from openai.types.chat import ChatCompletionMessageParam

from diff_analyzer.errors import InputError

DIFF_BEGIN = "<diff>"
DIFF_END = "</diff>"


@dataclass(frozen=True, slots=True)
class DefaultPrompt:
    """The prompt bundled with the package (`prompts/default.md`)."""


@dataclass(frozen=True, slots=True)
class InlinePrompt:
    """Prompt text given on the command line (`-p/--prompt`)."""

    text: str


@dataclass(frozen=True, slots=True)
class PromptFile:
    """A prompt read from a UTF-8 file (`--prompt-file`)."""

    path: Path


PromptSource = DefaultPrompt | InlinePrompt | PromptFile


def resolve_prompt(source: PromptSource) -> str:
    """Return the prompt text from `source`."""
    match source:
        case DefaultPrompt():
            text = files("diff_analyzer").joinpath("prompts/default.md").read_text("utf-8")
            blank = "O prompt padrão está vazio."
        case InlinePrompt(text):
            blank = "O prompt passado em -p/--prompt está vazio."
        case PromptFile(path):
            text = _read_prompt_file(path)
            blank = f"O arquivo de prompt {path} está vazio."
    text = text.strip()
    if not text:
        raise InputError(blank)
    return text


def build_messages(prompt: str, diff: str) -> list[ChatCompletionMessageParam]:
    """Return the chat messages."""
    newline = "" if diff.endswith("\n") else "\n"
    return [
        {"role": "system", "content": prompt},
        {"role": "user", "content": f"{DIFF_BEGIN}\n{diff}{newline}{DIFF_END}"},
    ]


def _read_prompt_file(path: Path) -> str:
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        raise InputError(f"Arquivo de prompt não encontrado: {path}") from None
    except OSError as exc:
        raise InputError(
            f"Não foi possível ler o arquivo de prompt {path} ({exc.strerror})."
        ) from None
    try:
        # utf-8-sig drops a leading byte order mark.
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise InputError(f"O arquivo de prompt {path} não está em UTF-8.") from None
