"""Settings read from `.env`, with code defaults."""

import io
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from dotenv.parser import Binding, parse_stream
from pydantic import Field, HttpUrl, SecretStr, TypeAdapter, ValidationError, field_validator
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)
from pydantic_settings.sources.utils import parse_env_vars

from diff_analyzer.errors import ConfigurationError

AuthMode = Literal["required", "none"]
DataEgress = Literal["local", "external"]

_ENV_PREFIX = "LLM_"
_HTTP_URL: TypeAdapter[HttpUrl] = TypeAdapter(HttpUrl)

# Fixed text since messages must not echo the value that failed.
_REASONS = {
    "base_url": "use uma URL http ou https",
    "model": "informe o nome do modelo do provedor",
    "auth": "use required ou none",
    "data_egress": "use local ou external",
    "timeout_connect": "use um número de segundos maior que 0",
    "timeout_read": "use um número de segundos maior que 0",
    "temperature": "use um número entre 0 e 2",
    "seed": "use um inteiro maior ou igual a 0",
    "max_input_chars": "use um inteiro maior ou igual a 1",
    "max_output_tokens": "use um inteiro maior ou igual a 1",
}


class _LiteralDotEnvSource(DotEnvSettingsSource):
    # Unknown keys are checked here: the stock source skips empty values before
    # `extra="forbid"` can see them.
    def _read_env_file(self, file_path: Path) -> Mapping[str, str | None]:
        known = {(self.env_prefix + name).upper() for name in self.settings_cls.model_fields}
        file_vars: dict[str, str | None] = {}
        problems: dict[str, str] = {}
        text = file_path.read_text(encoding=self.env_file_encoding)
        for binding in parse_stream(io.StringIO(text)):
            if binding.error:
                problems[f"linha {_statement_line(binding)}"] = "sintaxe inválida"
            elif binding.key is not None:
                if binding.key.upper() not in known:
                    problems[binding.key.upper()] = "chave desconhecida"
                file_vars[binding.key] = binding.value
        if problems:
            raise _config_error(problems)
        return parse_env_vars(
            file_vars, self.case_sensitive, self.env_ignore_empty, self.env_parse_none_str
        )


class Settings(BaseSettings):
    """LLM settings. Each field `x` is read from the `.env` key `LLM_X`.

    `api_key` is a `SecretStr`.
    """

    model_config = SettingsConfigDict(
        env_prefix=_ENV_PREFIX,
        env_ignore_empty=True,
        extra="forbid",
        hide_input_in_errors=True,
    )

    base_url: str = "https://api.openai.com/v1"
    model: str
    auth: AuthMode = "required"
    api_key: SecretStr = SecretStr("")
    data_egress: DataEgress = "external"
    timeout_connect: float = Field(default=5, gt=0)
    timeout_read: float = Field(default=120, gt=0)
    temperature: float = Field(default=0, ge=0, le=2)
    seed: int = Field(default=42, ge=0)
    max_input_chars: int = Field(default=60000, ge=1)
    max_output_tokens: int = Field(default=1024, ge=1)

    @field_validator("base_url")
    @classmethod
    def _require_http_url(cls, value: str) -> str:
        try:
            _HTTP_URL.validate_python(value)
        except ValidationError:
            raise ValueError("not an http(s) URL") from None
        return value

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Only code values and `.env`. The file is given to our source alone so the
        # stock dotenv source, built before this hook runs, never parses it.
        return (
            init_settings,
            _LiteralDotEnvSource(settings_cls, env_file=".env", env_file_encoding="utf-8"),
        )


def load_settings() -> Settings:
    """Read `.env` from the current directory over the code defaults."""
    try:
        return Settings()
    except ValidationError as exc:
        problems = _describe(exc)
    raise _config_error(problems) from None


def _describe(exc: ValidationError) -> dict[str, str]:
    problems: dict[str, str] = {}
    for error in exc.errors(include_input=False, include_url=False, include_context=False):
        # Unknown `.env` keys never get here: `_LiteralDotEnvSource` rejects them first.
        name = str(error["loc"][0])
        problems[_ENV_PREFIX + name.upper()] = _REASONS.get(name, "valor inválido")
    return problems


def _config_error(problems: dict[str, str]) -> ConfigurationError:
    details = "; ".join(f"{where} ({reason})" for where, reason in problems.items())
    return ConfigurationError(f"Configuração inválida no .env: {details}.")


def _statement_line(binding: Binding) -> int:
    # The parser counts from the blank lines before a statement, not the statement.
    text = binding.original.string
    return binding.original.line + text[: len(text) - len(text.lstrip())].count("\n")
