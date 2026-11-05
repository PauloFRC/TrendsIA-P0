#!/usr/bin/env bash
# Runs every quality check before a commit
set -euo pipefail

cd "$(dirname "$0")/.."

step() { printf '\n==> %s\n' "$1"; }

step "Lockfile matches pyproject.toml"
uv lock --check

step "Lint (ruff check)"
uv run --frozen ruff check .

step "Format (ruff format --check)"
uv run --frozen ruff format --check .

step "Type check (mypy)"
uv run --frozen mypy

step "Tests + coverage (minimum in pyproject.toml)"
uv run --frozen pytest --cov --cov-report=term-missing

step "Secret scan (gitleaks)"
# Untracked files are not scanned: run this after `git add`.
if ! command -v gitleaks >/dev/null 2>&1; then
    echo "gitleaks not found on PATH. Install it: https://github.com/gitleaks/gitleaks#installing" >&2
    exit 1
fi
gitleaks git --no-banner --redact .
gitleaks git --no-banner --redact --staged .
gitleaks git --no-banner --redact --pre-commit .

printf '\nAll checks passed.\n'
