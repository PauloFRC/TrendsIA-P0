"""Tests for the OpenAI-compatible LLM client over mocked HTTP and local HTTPS."""

import json
import ssl
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import httpx2
import openai
import pytest
import trustme
from openai.types.chat import ChatCompletionMessageParam
from pydantic import SecretStr

from diff_analyzer.config import Settings
from diff_analyzer.errors import (
    ConfigurationError,
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


class _ChatHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        self.rfile.read(int(self.headers["Content-Length"]))
        body = completion().content
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def mismatched_https_endpoint(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    trustme.CA().issue_cert("ingress.local").configure_cert(context)
    with ThreadingHTTPServer(("127.0.0.1", 0), _ChatHandler) as server:
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"https://127.0.0.1:{server.server_port}/v1"
        finally:
            server.shutdown()
            thread.join()


def test_tls_verification_rejects_untrusted_mismatched_certificate_by_default(
    mismatched_https_endpoint: str,
) -> None:
    settings = Settings(base_url=mismatched_https_endpoint, model="m", auth="none")

    with pytest.raises(LLMUnreachableError):
        OpenAIClient(settings).complete(MESSAGES)


def test_tls_verification_can_be_disabled_for_untrusted_mismatched_certificate(
    mismatched_https_endpoint: str,
) -> None:
    settings = Settings(
        base_url=mismatched_https_endpoint, model="m", auth="none", tls_verify=False
    )

    assert OpenAIClient(settings).complete(MESSAGES) == LLMResponse("Análise.", "stop")


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
    settings = Settings(model="modelo:3b", auth="none")
    client = client_for(settings, lambda _: completion("Sem problemas.", finish_reason))

    assert client.complete(MESSAGES) == LLMResponse("Sem problemas.", finish_reason)


def test_null_content_is_returned_as_empty_text() -> None:
    settings = Settings(model="modelo:3b", auth="none")
    client = client_for(settings, lambda _: completion(None))

    assert client.complete(MESSAGES) == LLMResponse("", "stop")


SHELL_SDK_SETTINGS = {
    "OPENAI_API_KEY": "sk-from-shell",
    "OPENAI_ADMIN_KEY": "sk-admin-from-shell",
    "OPENAI_BASE_URL": "http://other.test/v1",
    "OPENAI_ORG_ID": "org-from-shell",
    "OPENAI_PROJECT_ID": "proj-from-shell",
    "OPENAI_CUSTOM_HEADERS": (
        "Authorization: Bearer sk-from-shell\napi-key: sk-from-shell\nX-From-Shell: 1"
    ),
}


def sent_request(settings: Settings) -> httpx2.Request:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return completion()

    client_for(settings, handler).complete(MESSAGES)
    [request] = requests
    return request


@pytest.mark.parametrize(
    ("auth", "bearer"),
    [("required", SENTINEL), ("none", "unused")],
)
def test_request_uses_only_env_file_settings_whatever_the_shell_sets_for_the_sdk(
    monkeypatch: pytest.MonkeyPatch, auth: str, bearer: str
) -> None:
    for name in SHELL_SDK_SETTINGS:
        monkeypatch.delenv(name, raising=False)
    settings = Settings.model_validate(
        {"base_url": "http://llm.test/v1", "model": "modelo:3b", "auth": auth, "api_key": SENTINEL}
    )
    clean = sent_request(settings)
    for name, value in SHELL_SDK_SETTINGS.items():
        monkeypatch.setenv(name, value)

    request = sent_request(settings)

    assert request.headers["Authorization"] == f"Bearer {bearer}"
    assert dict(request.headers) == dict(clean.headers)
    assert request.url == clean.url


@pytest.mark.parametrize("api_key", ["", "   "], ids=["empty", "blank"])
def test_required_auth_without_a_key_is_a_configuration_error_before_any_request(
    monkeypatch: pytest.MonkeyPatch, api_key: str
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-shell")
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return completion()

    settings = Settings.model_validate(
        {"model": "modelo:3b", "auth": "required", "api_key": api_key}
    )
    with pytest.raises(ConfigurationError) as excinfo:
        client_for(settings, handler).complete(MESSAGES)

    assert "LLM_API_KEY" in str(excinfo.value)
    assert ".env" in str(excinfo.value)
    assert requests == []


def test_connection_failure_is_an_unreachable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # The SDK retries connection errors; skip its backoff sleeps.
    monkeypatch.setattr("time.sleep", lambda _: None)

    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused", request=request)

    settings = Settings(model="modelo:3b", auth="none")
    with pytest.raises(LLMUnreachableError) as excinfo:
        client_for(settings, handler).complete(MESSAGES)

    message = str(excinfo.value)
    assert "APIConnectionError" in message
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__suppress_context__


def test_timeout_is_an_unreachable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("time.sleep", lambda _: None)

    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("timed out", request=request)

    with pytest.raises(LLMUnreachableError, match="APITimeoutError"):
        client_for(Settings(model="modelo:3b", auth="none"), handler).complete(MESSAGES)


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


def bad_request(code: str, param: object = None) -> Handler:
    def handler(request: httpx2.Request) -> httpx2.Response:
        error = {
            "message": f"too long {SENTINEL}",
            "type": "invalid_request_error",
            "param": param,
            "code": code,
        }
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
    ("code", "param", "fix"),
    [
        ("unsupported_parameter", "max_tokens", "LLM_MODEL"),
        ("unsupported_value", "temperature", "LLM_TEMPERATURE"),
        ("unsupported_value", "max_tokens", "LLM_MAX_OUTPUT_TOKENS"),
    ],
)
def test_rejected_request_parameter_is_named_with_the_setting_to_change(
    code: str, param: str, fix: str
) -> None:
    settings = Settings(model="modelo:3b", auth="none")
    client = client_for(settings, bad_request(code, param))

    with pytest.raises(LLMResponseError) as excinfo:
        client.complete(MESSAGES)

    message = str(excinfo.value)
    assert param in message
    assert fix in message
    assert "modelo:3b" in message
    assert "400" in message
    assert SENTINEL not in message


@pytest.mark.parametrize(
    "param", [SENTINEL, [], {"name": "max_tokens"}], ids=["unknown-name", "list", "dict"]
)
def test_rejected_parameter_outside_the_sent_names_keeps_the_generic_error(
    param: object,
) -> None:
    settings = Settings(model="modelo:3b", auth="none")
    client = client_for(settings, bad_request("unsupported_parameter", param))

    with pytest.raises(LLMResponseError) as excinfo:
        client.complete(MESSAGES)

    message = str(excinfo.value)
    assert "BadRequestError" in message
    assert SENTINEL not in message


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
        model="modelo:3b",
        auth="required",
        api_key=SecretStr(SENTINEL),
    )

    with pytest.raises(DiffAnalyzerError) as excinfo:
        client_for(settings, handler).complete(MESSAGES)

    assert SENTINEL not in str(excinfo.value)


@pytest.mark.parametrize(
    "handler",
    [leaky_status(401), leaky_status(403), leaky_connection_error],
    ids=["auth-error", "permission-error", "connection-error"],
)
def test_endpoint_is_shown_by_scheme_host_and_port_only(
    monkeypatch: pytest.MonkeyPatch, handler: Handler
) -> None:
    monkeypatch.setattr("time.sleep", lambda _: None)
    settings = Settings(
        base_url=f"https://user:{SENTINEL}@llm.test:8443/{SENTINEL}/v1?token={SENTINEL}#{SENTINEL}",
        model="modelo:3b",
        auth="none",
    )

    with pytest.raises(DiffAnalyzerError) as excinfo:
        client_for(settings, handler).complete(MESSAGES)

    message = str(excinfo.value)
    assert "em https://llm.test:8443 " in message
    assert SENTINEL not in message
    assert "/v1" not in message
