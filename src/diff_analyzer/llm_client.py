"""LLM client protocol and its OpenAI-compatible implementation."""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit

import httpx2
import openai
from openai.types.chat import ChatCompletionMessageParam

from diff_analyzer.config import Settings
from diff_analyzer.errors import LLMAuthenticationError, LLMResponseError, LLMUnreachableError

# The SDK needs a key even when the endpoint has no auth.
_NO_AUTH_KEY = "unused"


@dataclass(frozen=True, slots=True)
class LLMResponse:
    """The model's answer and why it stopped (`"stop"`, `"length"`, ...)."""

    text: str
    finish_reason: str | None


class LLMClient(Protocol):
    """Sends chat messages to a model and returns its answer."""

    def complete(self, messages: Sequence[ChatCompletionMessageParam]) -> LLMResponse:
        """Return the model's answer to `messages`; raise only domain errors."""
        ...


class OpenAIClient:
    """`LLMClient` for any OpenAI-compatible endpoint, configured from `Settings`.

    `http_client` replaces the SDK's HTTP client.
    """

    def __init__(self, settings: Settings, http_client: httpx2.Client | None = None) -> None:
        if settings.auth == "required":
            api_key = settings.api_key.get_secret_value()
        else:
            api_key = _NO_AUTH_KEY
        self._settings = settings
        self._sdk = openai.OpenAI(
            base_url=settings.base_url, api_key=api_key, http_client=http_client
        )

    def complete(self, messages: Sequence[ChatCompletionMessageParam]) -> LLMResponse:
        """Send `messages` with the model and sampling settings; return the first choice."""
        settings = self._settings
        try:
            completion = self._sdk.chat.completions.create(
                model=settings.model,
                messages=messages,
                temperature=settings.temperature,
                seed=settings.seed,
                max_tokens=settings.max_output_tokens,
            )
        # Messages hold safe fields only.
        except openai.APIConnectionError as exc:
            raise LLMUnreachableError(
                f"Não foi possível falar com o LLM em {_safe_url(settings.base_url)} "
                f"({type(exc).__name__}). Verifique se o servidor está rodando."
            ) from None
        except (openai.AuthenticationError, openai.PermissionDeniedError) as exc:
            raise LLMAuthenticationError(
                f"O LLM em {_safe_url(settings.base_url)} recusou a credencial "
                f"(HTTP {exc.status_code}, {type(exc).__name__})."
            ) from None
        except openai.OpenAIError as exc:
            status = f" HTTP {exc.status_code}" if isinstance(exc, openai.APIStatusError) else ""
            raise LLMResponseError(
                f"O LLM retornou erro{status} para o modelo {settings.model} "
                f"({type(exc).__name__})."
            ) from None
        # The SDK parses a JSON body without catching decode errors.
        except json.JSONDecodeError:
            raise _unusable_response(settings.model) from None
        try:
            choice = completion.choices[0]
            content, finish_reason = choice.message.content, choice.finish_reason
        except (AttributeError, LookupError, TypeError):
            raise _unusable_response(settings.model) from None
        if content is None:
            return LLMResponse("", finish_reason)
        if not isinstance(content, str):
            raise _unusable_response(settings.model) from None
        return LLMResponse(content, finish_reason)


def _unusable_response(model: str) -> LLMResponseError:
    return LLMResponseError(
        f"O LLM retornou uma resposta sem conteúdo utilizável para o modelo {model}."
    )


def _safe_url(url: str) -> str:
    parts = urlsplit(url)
    host = parts.netloc.rpartition("@")[2]
    return urlunsplit((parts.scheme, host, parts.path, "", ""))
