FROM ghcr.io/astral-sh/uv:0.11.17 AS uv

FROM python:3.12.16-slim-trixie AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock .python-version README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12.16-slim-trixie
WORKDIR /app
COPY --from=build /app/.venv ./.venv
COPY examples ./examples
COPY .env.container ./.env
ENV PATH=/app/.venv/bin:$PATH
# Fail the build, not the run after an upload, when .env.container is invalid.
RUN python -c "from diff_analyzer.config import load_settings; from diff_analyzer.llm_client import OpenAIClient; OpenAIClient(load_settings())"
USER nobody
CMD ["diff-analyzer", "examples/exemplo.diff"]
