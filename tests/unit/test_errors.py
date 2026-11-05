"""Tests for the domain error hierarchy and its exit codes."""

import pytest

from diff_analyzer.errors import (
    ConfigurationError,
    DiffAnalyzerError,
    InputError,
    LLMAuthenticationError,
    LLMResponseError,
    LLMUnreachableError,
)


@pytest.mark.parametrize(
    ("error_class", "exit_code"),
    [
        (InputError, 3),
        (ConfigurationError, 4),
        (LLMAuthenticationError, 5),
        (LLMUnreachableError, 6),
        (LLMResponseError, 7),
    ],
)
def test_each_error_maps_to_its_exit_code(
    error_class: type[DiffAnalyzerError], exit_code: int
) -> None:
    err = error_class("mensagem para o usuário")

    assert isinstance(err, DiffAnalyzerError)
    assert err.exit_code == exit_code
    assert err.message == "mensagem para o usuário"
    assert str(err) == "mensagem para o usuário"


@pytest.mark.parametrize("message", ["", "   \n"])
def test_blank_message_is_rejected(message: str) -> None:
    with pytest.raises(ValueError, match="message"):
        InputError(message)


def test_base_class_cannot_be_raised_directly() -> None:
    with pytest.raises(TypeError, match="subclass"):
        DiffAnalyzerError("mensagem")
