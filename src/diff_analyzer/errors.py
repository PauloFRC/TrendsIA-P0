"""Domain exceptions that layers raise and only `cli.py` maps to exit codes."""

from typing import ClassVar


class DiffAnalyzerError(Exception):
    """Base of all domain errors.

    The message is shown to the user. Never put secrets, config values or `str(cause)` in it.
    Raise from SDK or pydantic errors with `from None`.
    """

    exit_code: ClassVar[int]

    def __init__(self, message: str) -> None:
        if type(self) is DiffAnalyzerError:
            raise TypeError("DiffAnalyzerError must be raised through a subclass")
        if not message.strip():
            raise ValueError("message must not be blank")
        super().__init__(message)
        self.message = message

    def __str__(self) -> str:
        return self.message


class InputError(DiffAnalyzerError):
    """Invalid or empty input diff."""

    exit_code = 3


class ConfigurationError(DiffAnalyzerError):
    """Missing or invalid configuration, credential, or prerequisite such as `git`."""

    exit_code = 4


class LLMAuthenticationError(DiffAnalyzerError):
    """The LLM endpoint rejected the credentials (401/403)."""

    exit_code = 5


class LLMUnreachableError(DiffAnalyzerError):
    """The LLM endpoint could not be reached or timed out."""

    exit_code = 6


class LLMResponseError(DiffAnalyzerError):
    """The LLM returned an error or an unusable response (404 model, 429, 5xx, empty)."""

    exit_code = 7
