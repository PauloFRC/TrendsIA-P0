"""Tests for reading the diff from a file or git repository."""

import os
import shutil
import subprocess
import sys
import traceback
from collections.abc import Callable
from pathlib import Path

import pytest

from diff_analyzer.diff_source import Commit, GitMode, Range, Staged, WorkingTree, read_diff
from diff_analyzer.errors import ConfigurationError, InputError
from diff_analyzer.llm_client import LLMResponse
from diff_analyzer.service import AnalysisRequest, analyze
from fakes import FakeLLMClient

GIT = shutil.which("git") or "git"
FILE_DIFF = "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n"


def traceback_text(exc: BaseException) -> str:
    return "".join(traceback.format_exception(exc))


@pytest.fixture(autouse=True)
def hermetic_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Inherited GIT_* variables (GIT_DIR, GIT_INDEX_FILE, ...), the user's git config, any
    # repository above tmp_path and the user's locale must not leak in.
    for name in [name for name in os.environ if name.startswith("GIT_")]:
        monkeypatch.delenv(name)
    monkeypatch.setenv("LC_ALL", "C")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-global-gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "test@example.com")


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(  # noqa: S603
        [GIT, "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return result.stdout


def commit_all(repo: Path, message: str) -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def marker_program(tmp_path: Path, name: str) -> tuple[Path, Path]:
    """Executable that creates the returned marker file when anything runs it."""
    marker = tmp_path / f"{name}.ran"
    program = tmp_path / f"{name}.py"
    program.write_text(
        f"#!{sys.executable}\nimport pathlib\npathlib.Path({str(marker)!r}).touch()\n",
        encoding="utf-8",
    )
    program.chmod(0o755)
    return program, marker


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Repository with commits c1 (a.txt=1) and c2 (a.txt=2, b.txt=1)."""
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    write(root / "a.txt", "1\n")
    commit_all(root, "c1")
    write(root / "a.txt", "2\n")
    write(root / "b.txt", "1\n")
    commit_all(root, "c2")
    return root


@pytest.fixture
def superproject(tmp_path: Path, repo: Path) -> Path:
    """`repo` with a committed submodule `sub` (s.txt=a)."""
    origin = tmp_path / "sub-origin"
    origin.mkdir()
    git(origin, "init", "-q")
    write(origin / "s.txt", "a\n")
    commit_all(origin, "s1")
    git(repo, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(origin), "sub")
    commit_all(repo, "add sub")
    return repo


def test_file_source_returns_its_content(tmp_path: Path) -> None:
    diff_file = tmp_path / "change.diff"
    diff_file.write_text(FILE_DIFF, encoding="utf-8")

    assert read_diff(str(diff_file)) == FILE_DIFF


def test_invalid_utf8_is_replaced(tmp_path: Path) -> None:
    diff_file = tmp_path / "latin1.diff"
    diff_file.write_bytes(b"+caf\xe9\n")

    assert read_diff(str(diff_file)) == "+caf\ufffd\n"


def test_missing_file_is_an_input_error(tmp_path: Path) -> None:
    missing = tmp_path / "missing.diff"

    with pytest.raises(InputError, match=r"missing\.diff"):
        read_diff(str(missing))


def test_unreadable_path_is_an_input_error(tmp_path: Path) -> None:
    not_a_dir = tmp_path / "file.diff"
    not_a_dir.write_text(FILE_DIFF, encoding="utf-8")

    with pytest.raises(InputError, match=r"file\.diff"):
        read_diff(str(not_a_dir / "inner.diff"))


@pytest.mark.parametrize("mode", [Staged(), Commit("HEAD"), Range("a..b")])
def test_git_mode_without_repository_is_an_input_error(tmp_path: Path, mode: GitMode) -> None:
    diff_file = tmp_path / "change.diff"
    diff_file.write_text(FILE_DIFF, encoding="utf-8")

    with pytest.raises(InputError, match="reposit"):
        read_diff(str(diff_file), mode)


@pytest.mark.parametrize("mode", [Staged(), Commit("HEAD"), Range("a..b")])
def test_git_mode_with_missing_source_reports_not_found(tmp_path: Path, mode: GitMode) -> None:
    with pytest.raises(InputError, match=r"não encontrado: .*missing"):
        read_diff(str(tmp_path / "missing"), mode)


def test_working_tree_diffs_staged_and_unstaged_against_head(repo: Path) -> None:
    write(repo / "a.txt", "staged\n")
    git(repo, "add", "a.txt")
    write(repo / "b.txt", "unstaged\n")

    diff = read_diff(str(repo), WorkingTree())

    assert diff == git(repo, "diff", "HEAD")
    assert "+staged" in diff
    assert "+unstaged" in diff


def test_working_tree_is_the_default_mode(repo: Path) -> None:
    write(repo / "a.txt", "changed\n")

    assert read_diff(str(repo)) == git(repo, "diff", "HEAD")


def test_staged_diffs_only_the_index(repo: Path) -> None:
    write(repo / "a.txt", "staged\n")
    git(repo, "add", "a.txt")
    write(repo / "b.txt", "unstaged\n")

    diff = read_diff(str(repo), Staged())

    assert "+staged" in diff
    assert "+unstaged" not in diff


def test_staged_ignores_later_changes_to_the_same_file(repo: Path) -> None:
    write(repo / "a.txt", "staged\n")
    git(repo, "add", "a.txt")
    write(repo / "a.txt", "unstaged\n")

    diff = read_diff(str(repo), Staged())

    assert "+staged" in diff
    assert "+unstaged" not in diff


def test_commit_shows_that_commit_only(repo: Path) -> None:
    diff = read_diff(str(repo), Commit("HEAD"))

    assert diff == git(repo, "show", "--format=", "HEAD")
    assert "+++ b/b.txt" in diff


def test_commit_works_on_the_first_commit(repo: Path) -> None:
    diff = read_diff(str(repo), Commit("HEAD~1"))

    assert "+++ b/a.txt" in diff
    assert "+1" in diff
    assert "b.txt" not in diff


@pytest.mark.parametrize("ref", ["a.txt", "HEAD:a.txt"])
def test_commit_ref_that_is_not_a_commit_is_an_input_error(repo: Path, ref: str) -> None:
    with pytest.raises(InputError):
        read_diff(str(repo), Commit(ref))


def test_commit_ref_wins_over_a_file_with_the_same_name(repo: Path) -> None:
    git(repo, "branch", "release")
    write(repo / "release", "file\n")
    commit_all(repo, "c3")

    assert read_diff(str(repo), Commit("release")) == git(repo, "show", "--format=", "HEAD~1")


def test_commit_of_annotated_tag_is_the_commit_patch_only(repo: Path) -> None:
    git(repo, "tag", "-a", "v1", "-m", "tag message")

    diff = read_diff(str(repo), Commit("v1"))

    assert diff == read_diff(str(repo), Commit("HEAD"))
    assert "tag message" not in diff


def test_working_tree_with_a_file_named_head(repo: Path) -> None:
    write(repo / "HEAD", "file\n")
    commit_all(repo, "c3")
    write(repo / "a.txt", "changed\n")

    assert "+changed" in read_diff(str(repo))


def test_range_diffs_between_refs(repo: Path) -> None:
    write(repo / "c.txt", "1\n")
    commit_all(repo, "c3")

    diff = read_diff(str(repo), Range("HEAD~2..HEAD~1"))

    assert diff == git(repo, "diff", "HEAD~2..HEAD~1")
    assert "c.txt" not in diff


@pytest.mark.parametrize("mode", [WorkingTree(), Staged(), Commit("HEAD"), Range("HEAD~1..HEAD")])
def test_nested_source_gives_whole_repository_diff_despite_diff_relative(
    repo: Path, mode: GitMode
) -> None:
    write(repo / "sub" / "inner.txt", "1\n")
    commit_all(repo, "c3")
    write(repo / "sub" / "inner.txt", "2\n")
    write(repo / "a.txt", "outside\n")
    commit_all(repo, "c4")
    write(repo / "sub" / "inner.txt", "3\n")
    write(repo / "a.txt", "outside again\n")
    git(repo, "add", "-A")
    git(repo, "config", "diff.relative", "true")

    diff = read_diff(str(repo / "sub"), mode)

    assert "+++ b/a.txt" in diff
    assert "+++ b/sub/inner.txt" in diff


def test_checkout_mismatch_is_rejected_before_sending_diff(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(repo / "a.txt", "ordinary edit\n")
    run = subprocess.run

    def git_with_unexpected_root(
        args: list[str], *, capture_output: bool, check: bool, env: dict[str, str]
    ) -> subprocess.CompletedProcess[bytes]:
        if args[-2:] == ["rev-parse", "--show-toplevel"]:
            return subprocess.CompletedProcess(args, 0, os.fsencode(repo.parent) + b"\n", b"")
        return run(args, capture_output=capture_output, check=check, env=env)

    monkeypatch.setattr(subprocess, "run", git_with_unexpected_root)
    client = FakeLLMClient(LLMResponse("Resumo.", "stop"))

    with pytest.raises(InputError, match="raiz da árvore de trabalho"):
        analyze(AnalysisRequest(str(repo)), client)

    assert client.calls == []


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("color.ui", "always"),
        ("color.diff", "always"),
        ("diff.noprefix", "true"),
        ("diff.mnemonicPrefix", "true"),
        ("diff.external", "echo EXTERNAL"),
    ],
)
def test_user_git_config_does_not_change_the_output(repo: Path, key: str, value: str) -> None:
    write(repo / "a.txt", "changed\n")
    expected = read_diff(str(repo))
    git(repo, "config", key, value)

    assert read_diff(str(repo)) == expected


def test_textconv_is_not_applied(repo: Path) -> None:
    write(repo / ".gitattributes", "*.txt diff=upper\n")
    commit_all(repo, "attributes")
    git(repo, "config", "diff.upper.textconv", "tr a-z A-Z <")
    write(repo / "a.txt", "lower\n")

    assert "+lower" in read_diff(str(repo))


@pytest.mark.parametrize("mode", [WorkingTree(), Staged()])
def test_repository_fsmonitor_never_runs(tmp_path: Path, repo: Path, mode: GitMode) -> None:
    write(repo / "a.txt", "changed\n")
    git(repo, "add", "a.txt")
    program, marker = marker_program(tmp_path, "fsmonitor")
    git(repo, "config", "core.fsmonitor", str(program))

    read_diff(str(repo), mode)

    assert not marker.exists()


@pytest.mark.parametrize("variable", ["clean", "process"])
@pytest.mark.parametrize("defined_in", ["repository config", "included file"])
def test_repository_filter_never_runs(
    tmp_path: Path, repo: Path, variable: str, defined_in: str
) -> None:
    write(repo / ".gitattributes", "*.txt filter=evil\n")
    commit_all(repo, "attributes")
    program, marker = marker_program(tmp_path, "filter")
    if defined_in == "included file":
        included = tmp_path / "included.gitconfig"
        git(repo, "config", "--file", str(included), f"filter.evil.{variable}", str(program))
        git(repo, "config", "include.path", str(included))
    else:
        git(repo, "config", f"filter.evil.{variable}", str(program))
    git(repo, "config", "filter.evil.required", "true")
    write(repo / "a.txt", "changed\n")

    diff = read_diff(str(repo))

    assert not marker.exists()
    assert "+changed" in diff


def test_user_filter_runs_instead_of_the_repository_one(tmp_path: Path, repo: Path) -> None:
    write(repo / ".gitattributes", "*.txt filter=lfs\n")
    commit_all(repo, "attributes")
    user_program, user_marker = marker_program(tmp_path, "user-filter")
    repo_program, repo_marker = marker_program(tmp_path, "repo-filter")
    git(repo, "config", "--global", "filter.lfs.clean", str(user_program))
    git(repo, "config", "filter.lfs.clean", str(repo_program))
    write(repo / "a.txt", "changed\n")

    read_diff(str(repo))

    assert user_marker.exists()
    assert not repo_marker.exists()


def test_repository_filter_named_with_equals_is_refused(tmp_path: Path, repo: Path) -> None:
    # An "=" in the driver name splits the -c key early.
    write(repo / ".gitattributes", "*.txt filter=a=b\n")
    commit_all(repo, "attributes")
    program, marker = marker_program(tmp_path, "filter")
    git(repo, "config", "filter.a=b.clean", str(program))
    write(repo / "a.txt", "changed\n")

    with pytest.raises(InputError, match="filtro"):
        read_diff(str(repo))
    assert not marker.exists()


def test_repository_hook_never_runs(tmp_path: Path, repo: Path) -> None:
    program, marker = marker_program(tmp_path, "hook")
    shutil.copy(program, repo / ".git" / "hooks" / "post-index-change")
    # A newer mtime with unchanged content makes Git rewrite the index.
    stat = (repo / "a.txt").stat()
    os.utime(repo / "a.txt", (stat.st_atime + 10, stat.st_mtime + 10))

    read_diff(str(repo))

    assert not marker.exists()


def test_signature_check_program_never_runs(tmp_path: Path, repo: Path) -> None:
    header, _, message = git(repo, "cat-file", "commit", "HEAD").partition("\n\n")
    signature = " -----BEGIN PGP SIGNATURE-----\n \n x\n -----END PGP SIGNATURE-----"
    signed = subprocess.run(  # noqa: S603
        [GIT, "-C", str(repo), "hash-object", "-t", "commit", "-w", "--stdin"],
        input=f"{header}\ngpgsig{signature}\n\n{message}",
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    program, marker = marker_program(tmp_path, "gpg")
    git(repo, "config", "log.showSignature", "true")
    git(repo, "config", "gpg.program", str(program))

    read_diff(str(repo), Commit(signed))

    assert not marker.exists()


def test_partial_clone_never_fetches_missing_objects(tmp_path: Path, repo: Path) -> None:
    git(repo, "config", "uploadpack.allowFilter", "true")
    clone = tmp_path / "clone"
    git(
        tmp_path,
        "clone",
        "-q",
        "--no-local",
        "--filter=blob:none",
        "--no-checkout",
        repo.as_uri(),
        str(clone),
    )
    program, marker = marker_program(tmp_path, "upload-pack")
    git(clone, "config", "remote.origin.uploadpack", str(program))
    # A protocol-specific setting beats protocol.allow.
    git(clone, "config", "protocol.file.allow", "always")

    with pytest.raises(InputError):
        read_diff(str(clone), Commit("HEAD"))
    assert not marker.exists()


def test_submodule_config_never_runs_for_the_working_tree(
    tmp_path: Path, superproject: Path
) -> None:
    sub = superproject / "sub"
    write(sub / ".gitattributes", "*.txt filter=evil\n")
    commit_all(sub, "attributes")
    program, marker = marker_program(tmp_path, "submodule-filter")
    git(sub, "config", "filter.evil.clean", str(program))
    # Per-submodule settings beat diff.ignoreSubmodules.
    git(superproject, "config", "submodule.sub.ignore", "none")
    # A same-size edit forces Git to read the file.
    write(sub / "s.txt", "b\n")

    read_diff(str(superproject))

    assert not marker.exists()


def test_submodule_config_never_runs_for_a_commit(tmp_path: Path, superproject: Path) -> None:
    sub = superproject / "sub"
    write(sub / "s.txt", "b\n")
    commit_all(sub, "s2")
    commit_all(superproject, "bump sub")
    program, marker = marker_program(tmp_path, "submodule-external")
    git(sub, "config", "diff.external", str(program))
    git(superproject, "config", "diff.submodule", "diff")

    diff = read_diff(str(superproject), Commit("HEAD"))

    assert not marker.exists()
    assert "+Subproject commit" in diff


def test_git_without_config_scopes_is_refused(
    tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Git before 2.26 rejects --show-scope.
    old_bin = tmp_path / "old-bin"
    old_bin.mkdir()
    old_git = old_bin / "git"
    old_git.write_text(
        f"#!{sys.executable}\nimport os, sys\n"
        "if 'config' in sys.argv:\n"
        "    sys.stderr.write('error: unknown option show-scope\\n')\n"
        "    sys.exit(129)\n"
        f"os.execv({GIT!r}, [{GIT!r}, *sys.argv[1:]])\n",
        encoding="utf-8",
    )
    old_git.chmod(0o755)
    monkeypatch.setenv("PATH", f"{old_bin}{os.pathsep}{os.environ['PATH']}")
    write(repo / "a.txt", "changed\n")

    with pytest.raises(InputError, match="show-scope"):
        read_diff(str(repo))


def test_non_ascii_paths_are_not_quoted(repo: Path) -> None:
    write(repo / "caf\u00e9.txt", "1\n")
    git(repo, "add", "-A")

    assert "+++ b/caf\u00e9.txt" in read_diff(str(repo), Staged())


@pytest.mark.parametrize(
    ("variable", "other_path", "mode"),
    [
        ("GIT_DIR", ".git", Commit("HEAD")),
        ("GIT_COMMON_DIR", ".git", Commit("HEAD")),
        ("GIT_OBJECT_DIRECTORY", ".git/objects", Commit("HEAD")),
        ("GIT_WORK_TREE", "", WorkingTree()),
        ("GIT_INDEX_FILE", ".git/index", Staged()),
    ],
)
def test_inherited_git_variables_never_select_another_repository(
    tmp_path: Path,
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    variable: str,
    other_path: str,
    mode: GitMode,
) -> None:
    other = tmp_path / "other"
    other.mkdir()
    git(other, "init", "-q")
    write(other / "other.txt", "other\n")
    commit_all(other, "other")
    write(repo / "a.txt", "changed\n")
    git(repo, "add", "a.txt")
    expected = read_diff(str(repo), mode)
    monkeypatch.setenv(variable, str(other / other_path))

    assert read_diff(str(repo), mode) == expected


def test_git_missing_is_a_configuration_error_and_files_still_work(
    tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))
    diff_file = tmp_path / "change.diff"
    diff_file.write_text(FILE_DIFF, encoding="utf-8")

    with pytest.raises(ConfigurationError, match="Instale o git") as excinfo:
        read_diff(str(repo))

    assert excinfo.value.exit_code == 4
    assert read_diff(str(diff_file)) == FILE_DIFF


@pytest.mark.parametrize("source_kind", ["file", "clean repository"])
def test_empty_input_is_returned_as_empty_text(
    tmp_path: Path, repo: Path, source_kind: str
) -> None:
    empty_file = tmp_path / "empty.diff"
    empty_file.write_bytes(b"")
    sources = {"file": str(empty_file), "clean repository": str(repo)}

    assert read_diff(sources[source_kind]) == ""


def test_git_that_cannot_run_is_a_configuration_error_without_cause(
    tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad_bin = tmp_path / "bad-bin"
    bad_git = bad_bin / "git"
    bad_bin.mkdir()
    bad_git.write_bytes(b"\x00\x01not an executable format")
    bad_git.chmod(0o755)
    monkeypatch.setenv("PATH", str(bad_bin))

    with pytest.raises(ConfigurationError, match="git") as excinfo:
        read_diff(str(repo))

    assert "[Errno 8]" not in traceback_text(excinfo.value)


def test_directory_outside_a_repository_is_an_input_error(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()

    with pytest.raises(InputError, match=r"(?i)not a git repository"):
        read_diff(str(plain))


def test_unknown_ref_reports_git_error_line(repo: Path) -> None:
    with pytest.raises(InputError, match=r"fatal: .*no-such-ref") as excinfo:
        read_diff(str(repo), Commit("no-such-ref"))

    assert "\n" not in excinfo.value.message


def test_repository_without_commits_suggests_staged_which_works(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    git(empty, "init", "-q")
    write(empty / "a.txt", "1\n")
    git(empty, "add", "a.txt")

    with pytest.raises(InputError, match="--staged"):
        read_diff(str(empty))
    assert "+++ b/a.txt" in read_diff(str(empty), Staged())


@pytest.mark.parametrize(
    "build",
    [
        lambda: Commit("--output=/tmp/x"),
        lambda: Commit(""),
        lambda: Range("--output=/tmp/x..HEAD"),
        lambda: Range(""),
    ],
)
def test_ref_starting_with_dash_or_blank_is_rejected(build: Callable[[], GitMode]) -> None:
    with pytest.raises(InputError):
        build()


def test_range_without_dots_is_rejected() -> None:
    with pytest.raises(InputError, match=r"\.\."):
        Range("HEAD")
