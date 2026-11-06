"""Reads the diff from a file, stdin or git repository, and validates it."""

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from diff_analyzer.errors import ConfigurationError, InputError

STDIN_SOURCE = "-"

# Keep the output a plain, parsable whole-repository diff
_GIT_CONFIG = (
    "-c",
    "core.quotePath=false",
    "-c",
    "diff.noprefix=false",
    "-c",
    "diff.mnemonicPrefix=false",
    "-c",
    "diff.relative=false",
)
_DIFF_FLAGS = ("--no-color", "--no-ext-diff", "--no-textconv")
# Output of `git rev-parse --local-env-vars`: variables that bind git to one repository.
# Inherited, they override SOURCE (GIT_DIR disables discovery from `-C`).
_REPO_ENV_VARS = frozenset(
    {
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_CONFIG",
        "GIT_CONFIG_PARAMETERS",
        "GIT_CONFIG_COUNT",
        "GIT_OBJECT_DIRECTORY",
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_IMPLICIT_WORK_TREE",
        "GIT_GRAFT_FILE",
        "GIT_INDEX_FILE",
        "GIT_NO_REPLACE_OBJECTS",
        "GIT_REPLACE_REF_BASE",
        "GIT_PREFIX",
        "GIT_SHALLOW_FILE",
        "GIT_COMMON_DIR",
    }
)


@dataclass(frozen=True, slots=True)
class WorkingTree:
    """Staged and unstaged changes against the last commit (`git diff HEAD`)."""


@dataclass(frozen=True, slots=True)
class Staged:
    """Staged changes only (`git diff --cached`)."""


@dataclass(frozen=True, slots=True)
class Commit:
    """The changes of one commit (`git show --format= <ref>`)."""

    ref: str

    def __post_init__(self) -> None:
        _check_ref(self.ref, "--commit")


@dataclass(frozen=True, slots=True)
class Range:
    """Changes between two refs (`git diff <a>..<b>`)."""

    spec: str

    def __post_init__(self) -> None:
        _check_ref(self.spec, "--range")
        if ".." not in self.spec:
            raise InputError(f"--range espera o formato <a>..<b>, recebido: {self.spec}")


GitMode = WorkingTree | Staged | Commit | Range
_DEFAULT_MODE = WorkingTree()


def read_diff(source: str, mode: GitMode = _DEFAULT_MODE, stdin: BinaryIO | None = None) -> str:
    """Return the diff text from `source`: `-` (stdin), a directory (git repository) or a file.

    Git modes other than `WorkingTree` need a directory source. Invalid UTF-8 is replaced.
    Raises `InputError` for unreadable sources and git failures, `ConfigurationError`
    when `git` is needed but not on `PATH`.
    """
    path = Path(source)
    if source != STDIN_SOURCE and path.is_dir():
        return _read_git(path, mode)
    if not isinstance(mode, WorkingTree):
        raise InputError(
            "--staged, --commit e --range só valem quando SOURCE é um repositório git."
        )
    data = _read_stdin(stdin) if source == STDIN_SOURCE else _read_file(path)
    return data.decode("utf-8", errors="replace")


def _check_ref(ref: str, flag: str) -> None:
    if not ref.strip() or ref.startswith("-"):
        raise InputError(f"{flag} espera uma referência git que não comece com '-'.")


def _read_file(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        raise InputError(f"Arquivo não encontrado: {path}") from None
    except OSError as exc:
        raise InputError(f"Não foi possível ler o arquivo {path} ({exc.strerror}).") from None


def _read_stdin(stdin: BinaryIO | None) -> bytes:
    closed = InputError("O stdin está fechado; envie o diff por um pipe ou passe um arquivo.")
    # sys.stdin is None when file descriptor 0 is closed.
    if stdin is None and sys.stdin is None:
        raise closed
    try:
        return (stdin or sys.stdin.buffer).read()
    except ValueError:
        raise closed from None
    except OSError as exc:
        raise InputError(f"Não foi possível ler o stdin ({exc.strerror}).") from None


def _git_args(mode: GitMode) -> list[str]:
    # The trailing "--" stops git from reading a ref as a path; ^{commit} rejects
    # paths, blobs and trees, and skips the header of an annotated tag.
    match mode:
        case WorkingTree():
            return ["diff", *_DIFF_FLAGS, "HEAD", "--"]
        case Staged():
            return ["diff", *_DIFF_FLAGS, "--cached", "--"]
        case Commit(ref):
            return ["show", "--format=", *_DIFF_FLAGS, f"{ref}^{{commit}}", "--"]
        case Range(spec):
            return ["diff", *_DIFF_FLAGS, spec, "--"]


def _read_git(repo: Path, mode: GitMode) -> str:
    git = shutil.which("git")
    if git is None:
        raise ConfigurationError(
            "O git não foi encontrado no PATH. Instale o git para analisar um repositório, "
            "ou passe um arquivo .diff ou '-' (stdin)."
        )
    result = _run_git(git, repo, _git_args(mode))
    if result.returncode == 0:
        return result.stdout.decode("utf-8", errors="replace")
    if isinstance(mode, WorkingTree) and _has_no_commits(git, repo):
        raise InputError(f"O repositório {repo} ainda não tem commits; use --staged.")
    detail = _first_line(result.stderr)
    suffix = f": {detail}" if detail else "."
    raise InputError(f"Falha ao obter o diff do repositório {repo}{suffix}")


def _run_git(git: str, repo: Path, args: list[str]) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(  # noqa: S603
            [git, "-C", str(repo), "--no-pager", *_GIT_CONFIG, *args],
            capture_output=True,
            check=False,
            env={k: v for k, v in os.environ.items() if k not in _REPO_ENV_VARS},
        )
    except OSError as exc:
        raise ConfigurationError(
            f"O git em {git} não pôde ser executado ({exc.strerror}). Reinstale o git."
        ) from None


def _has_no_commits(git: str, repo: Path) -> bool:
    return _run_git(git, repo, ["rev-parse", "--verify", "--quiet", "HEAD"]).returncode == 1


def _first_line(stderr: bytes) -> str:
    text = stderr.decode("utf-8", errors="replace")
    return next((line.strip() for line in text.splitlines() if line.strip()), "")
