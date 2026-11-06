# AGENTS.md

## Project

`diff-analyzer`: Python CLI that sends a diff (file or git repository) to an OpenAI-compatible LLM over REST and prints the analysis. It stores nothing except the prompt markdown files.

- User docs (`README.md`, `RELATORIO_EXECUCAO.md`) are in Portuguese. Code, comments and commit messages are in English. Commits follow Conventional Commits.
- Agents do not commit, except on throwaway scratch branches. Never commit to `main`, merge, tag or push: the user does that.

## Environment

- Install with `uv sync --frozen`; run tools with `uv run --frozen ...`.
- Add dependencies with `uv add --bounds exact <package>`: all versions are pinned.
- Configuration comes only from `.env` (copy `.env.example`). Never commit `.env` or any credential.

## Validation

`scripts/validate.sh` must pass before every commit: lockfile check, ruff lint and format, strict mypy, pytest with coverage over 90%, gitleaks. It does not pass yet: the setup smoke tests that make it pass still need to be added.

## Tests

- Prefer test-driven development: write the failing test first, then the code. This is the default for bug fixes and changes to existing behaviour. For a brand-new feature whose shape is still unclear, tests may follow the code in the same commit.
- Unit tests never call a real LLM. Use a fake client, or `httpx2.MockTransport` for HTTP-level cases (`respx` does not intercept `openai` 3.x). Tests that need a real LLM are marked `live` and are excluded by default.

## Code style

- Every source file starts with a one-line docstring (or comment, for shell) saying what the file is for.
- Use `"""docstrings"""` only on public classes and functions that a user of the module needs to understand. No docstrings on private helpers.
- Comment only what the code does not make obvious, such as a reason or a constraint. Keep comments short and in plain, direct language.
- Use only ASCII characters. The one exception is accented letters in Portuguese text (user docs, prompts, user-facing messages).
