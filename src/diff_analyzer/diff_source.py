"""Reads the diff from a file or git repository, and validates it."""

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from diff_analyzer.errors import ConfigurationError, InputError

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
_NO_REPO_COMMANDS = (
    "-c",
    # Older Git treats "false" as a hook path.
    "core.fsmonitor=",
    "-c",
    f"core.hooksPath={os.devnull}",
    "-c",
    "log.showSignature=false",
)
# Keep Git out of submodule worktrees and configs.
_DIFF_FLAGS = (
    "--no-color",
    "--no-ext-diff",
    "--no-textconv",
    "--ignore-submodules=dirty",
    "--submodule=short",
)
_USER_SCOPES = frozenset({"system", "global"})
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


def read_diff(source: str, mode: GitMode = _DEFAULT_MODE) -> str:
    """Return the diff text from `source`: a directory (git repository) or a file."""
    path = Path(source)
    if path.is_dir():
        return _read_git(path, mode)
    if not isinstance(mode, WorkingTree):
        if not path.exists():
            raise InputError(f"Repositório não encontrado: {path}")
        raise InputError(
            "--staged, --commit e --range só valem quando SOURCE é um repositório git."
        )
    return _read_file(path).decode("utf-8", errors="replace")


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
            "ou passe um arquivo .diff."
        )
    result = _run_git(git, repo, [*_filter_overrides(git, repo), *_git_args(mode)])
    if result.returncode == 0:
        return result.stdout.decode("utf-8", errors="replace")
    if isinstance(mode, WorkingTree) and _has_no_commits(git, repo):
        raise InputError(f"O repositório {repo} ainda não tem commits; use --staged.")
    raise _git_failure(repo, result.stderr)


def _filter_overrides(git: str, repo: Path) -> list[str]:
    # Only override repo keys: even an empty `process` suppresses `clean`.
    listing = _run_git(git, repo, ["config", "-z", "--show-scope", "--get-regexp", r"^filter\."])
    # Exit 1 means no matches; older Git rejects --show-scope.
    if listing.returncode not in (0, 1):
        raise _git_failure(repo, listing.stderr)
    fields = listing.stdout.decode("utf-8", errors="surrogateescape").split("\0")[:-1]
    user_values: dict[str, str] = {}
    repo_keys: set[str] = set()
    for scope, entry in zip(fields[0::2], fields[1::2], strict=True):
        key, _, value = entry.partition("\n")
        if scope in _USER_SCOPES:
            user_values[key] = value
        else:
            repo_keys.add(key)
    overrides: list[str] = []
    for key in sorted(repo_keys):
        driver, _, name = key.removeprefix("filter.").rpartition(".")
        if "=" in driver:
            raise InputError(
                f"O repositório {repo} define o filtro '{driver}', com '=' no nome, "
                "que o diff-analyzer não consegue desativar."
            )
        default = "false" if name == "required" else ""
        overrides += ["-c", f"{key}={user_values.get(key, default)}"]
    return overrides


def _git_failure(repo: Path, stderr: bytes) -> InputError:
    detail = _first_line(stderr)
    suffix = f": {detail}" if detail else "."
    return InputError(f"Falha ao obter o diff do repositório {repo}{suffix}")


def _run_git(git: str, repo: Path, args: list[str]) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(  # noqa: S603
            [git, "-C", str(repo), "--no-pager", *_GIT_CONFIG, *_NO_REPO_COMMANDS, *args],
            capture_output=True,
            check=False,
            env=_git_env(),
        )
    except OSError as exc:
        raise ConfigurationError(
            f"O git em {git} não pôde ser executado ({exc.strerror}). Reinstale o git."
        ) from None


def _git_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in _REPO_ENV_VARS}
    # An empty allowlist blocks lazy fetches regardless of repo config.
    env["GIT_ALLOW_PROTOCOL"] = ""
    return env


def _has_no_commits(git: str, repo: Path) -> bool:
    return _run_git(git, repo, ["rev-parse", "--verify", "--quiet", "HEAD"]).returncode == 1


def _first_line(stderr: bytes) -> str:
    text = stderr.decode("utf-8", errors="replace")
    return next((line.strip() for line in text.splitlines() if line.strip()), "")
