"""Tests for the OpenAI-compatible LLM client, over a mocked HTTP transport."""

import json
from collections.abc import Callable
from pathlib import Path

import httpx2
import openai
import pytest
from openai.types.chat import ChatCompletionMessageParam
from pydantic import SecretStr

from diff_analyzer.config import Settings
from diff_analyzer.errors import (
    DiffAnalyzerError,
    LLMAuthenticationError,
    LLMResponseError,
    LLMUnreachableError,
)
from diff_analyzer.llm_client import LLMClient, LLMResponse, OpenAIClient

SENTINEL = "sk-SENTINEL-9f3a"
MESSAGES: list[ChatCompletionMessageParam] = [
    {"role": "system", "content": "Analise o diff."},
    {"role": "user", "content": "<diff>\n-a\n+b\n</diff>"},
]

Handler = Callable[[httpx2.Request], httpx2.Response]


@pytest.fixture(autouse=True)
def in_tmp_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Settings also read `.env` from the current directory.
    monkeypatch.chdir(tmp_path)


def completion(content: str | None = "Análise.", finish_reason: str = "stop") -> httpx2.Response:
    return httpx2.Response(
        200,
        json={
            "id": "chatcmpl-1",
            "object": "chat.completion",
            "created": 0,
            "model": "m",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": finish_reason,
                }
            ],
        },
    )


def client_for(settings: Settings, handler: Handler) -> LLMClient:
    # Typed as the protocol so mypy checks that OpenAIClient still implements it.
    transport = httpx2.MockTransport(handler)
    return OpenAIClient(settings, http_client=openai.DefaultHttpxClient(transport=transport))


def test_request_carries_model_messages_and_sampling_settings() -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return completion()

    settings = Settings(
        base_url="http://llm.test/v1",
        model="modelo:3b",
        auth="none",
        temperature=0.3,
        seed=7,
        max_output_tokens=99,
    )
    client_for(settings, handler).complete(MESSAGES)

    [request] = requests
    assert request.method == "POST"
    assert str(request.url) == "http://llm.test/v1/chat/completions"
    body = json.loads(request.content)
    assert {key: body.get(key) for key in body.keys() - {"stream"}} == {
        "model": "modelo:3b",
        "messages": MESSAGES,
        "temperature": 0.3,
        "seed": 7,
        "max_tokens": 99,
    }


@pytest.mark.parametrize("finish_reason", ["stop", "length"])
def test_response_is_parsed_into_text_and_finish_reason(finish_reason: str) -> None:
    settings = Settings(auth="none")
    client = client_for(settings, lambda _: completion("Sem problemas.", finish_reason))

    assert client.complete(MESSAGES) == LLMResponse("Sem problemas.", finish_reason)


def test_null_content_is_returned_as_empty_text() -> None:
    settings = Settings(auth="none")
    client = client_for(settings, lambda _: completion(None))

    assert client.complete(MESSAGES) == LLMResponse("", "stop")


@pytest.mark.parametrize(
    ("auth", "bearer"),
    [("required", SENTINEL), ("none", "unused")],
)
def test_api_key_comes_from_settings_in_required_mode_and_is_a_placeholder_in_none_mode(
    monkeypatch: pytest.MonkeyPatch, auth: str, bearer: str
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-environment")
    headers: list[str | None] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        headers.append(request.headers.get("Authorization"))
        return completion()

    settings = Settings.model_validate({"auth": auth, "api_key": SENTINEL})
    client_for(settings, handler).complete(MESSAGES)

    assert headers == [f"Bearer {bearer}"]


def test_connection_failure_is_an_unreachable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # The SDK retries connection errors; skip its backoff sleeps.
    monkeypatch.setattr("time.sleep", lambda _: None)

    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused", request=request)

    settings = Settings(base_url="http://llm.test:8080/v1", auth="none")
    with pytest.raises(LLMUnreachableError) as excinfo:
        client_for(settings, handler).complete(MESSAGES)

    message = str(excinfo.value)
    assert "http://llm.test:8080/v1" in message
    assert "APIConnectionError" in message
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__suppress_context__


def test_timeout_is_an_unreachable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("time.sleep", lambda _: None)

    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("timed out", request=request)

    with pytest.raises(LLMUnreachableError, match="APITimeoutError"):
        client_for(Settings(auth="none"), handler).complete(MESSAGES)


def test_http_error_is_a_response_error_naming_model_and_status() -> None:
    settings = Settings(model="modelo:3b", auth="none")
    client = client_for(settings, lambda _: httpx2.Response(404, json={"error": "not found"}))

    with pytest.raises(LLMResponseError) as excinfo:
        client.complete(MESSAGES)

    message = str(excinfo.value)
    assert "modelo:3b" in message
    assert "404" in message
    assert "NotFoundError" in message
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__suppress_context__


def bad_request(code: str) -> Handler:
    def handler(request: httpx2.Request) -> httpx2.Response:
        error = {"message": f"too long {SENTINEL}", "type": "invalid_request_error", "code": code}
        return httpx2.Response(400, json={"error": error})

    return handler


def test_context_length_rejection_is_a_response_error_about_server_context() -> None:
    settings = Settings(model="modelo:3b", auth="none")
    client = client_for(settings, bad_request("context_length_exceeded"))

    with pytest.raises(LLMResponseError) as excinfo:
        client.complete(MESSAGES)

    message = str(excinfo.value)
    assert "contexto suficiente" in message
    assert "modelo:3b" in message
    assert "400" in message
    assert SENTINEL not in message
    assert excinfo.value.__suppress_context__


def test_other_bad_request_keeps_the_generic_response_error() -> None:
    settings = Settings(model="modelo:3b", auth="none")
    client = client_for(settings, bad_request("invalid_value"))

    with pytest.raises(LLMResponseError) as excinfo:
        client.complete(MESSAGES)

    message = str(excinfo.value)
    assert "BadRequestError" in message
    assert "contexto" not in message


@pytest.mark.parametrize(
    ("status", "sdk_error"),
    [(401, "AuthenticationError"), (403, "PermissionDeniedError")],
)
def test_rejected_credentials_are_an_authentication_error(status: int, sdk_error: str) -> None:
    settings = Settings(model="modelo:3b", auth="none")
    client = client_for(settings, lambda _: httpx2.Response(status, json={"error": "denied"}))

    with pytest.raises(LLMAuthenticationError) as excinfo:
        client.complete(MESSAGES)

    message = str(excinfo.value)
    assert str(status) in message
    assert sdk_error in message
    assert excinfo.value.__suppress_context__


def choice_with_content(content: object) -> httpx2.Response:
    return httpx2.Response(200, json={"choices": [{"message": {"content": content}}]})


@pytest.mark.parametrize(
    "response",
    [
        httpx2.Response(200, json={"choices": []}),
        httpx2.Response(200, json={}),
        httpx2.Response(200, json={"choices": {}}),
        httpx2.Response(200, json={"choices": [None]}),
        httpx2.Response(200, json={"choices": [{"message": None, "finish_reason": "stop"}]}),
        choice_with_content(123),
        choice_with_content([{"type": "text", "text": "oi"}]),
        httpx2.Response(200, content=b"{oops", headers={"Content-Type": "application/json"}),
    ],
    ids=[
        "no-choices",
        "no-choices-key",
        "choices-not-a-list",
        "null-choice",
        "null-message",
        "number-content",
        "list-content",
        "invalid-json",
    ],
)
def test_success_status_without_a_usable_choice_is_a_response_error(
    response: httpx2.Response,
) -> None:
    settings = Settings(model="modelo:3b", auth="none")
    client = client_for(settings, lambda _: response)

    with pytest.raises(LLMResponseError, match="modelo:3b") as excinfo:
        client.complete(MESSAGES)

    assert excinfo.value.__suppress_context__


def leaky_status(status: int) -> Handler:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            status,
            json={"error": {"message": f"bad key {SENTINEL}"}},
            headers={"X-Echo": SENTINEL},
        )

    return handler


def leaky_connection_error(request: httpx2.Request) -> httpx2.Response:
    raise httpx2.ConnectError(SENTINEL, request=request)


@pytest.mark.parametrize(
    "handler",
    [leaky_status(400), leaky_status(401), leaky_connection_error],
    ids=["http-error", "auth-error", "connection-error"],
)
def test_error_messages_never_carry_secrets_bodies_headers_or_url_credentials(
    monkeypatch: pytest.MonkeyPatch, handler: Handler
) -> None:
    monkeypatch.setattr("time.sleep", lambda _: None)
    settings = Settings(
        base_url=f"http://user:{SENTINEL}@llm.test/v1?token={SENTINEL}",
        auth="required",
        api_key=SecretStr(SENTINEL),
    )

    with pytest.raises(DiffAnalyzerError) as excinfo:
        client_for(settings, handler).complete(MESSAGES)

    assert SENTINEL not in str(excinfo.value)
