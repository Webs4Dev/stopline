# Tests for the git helper against real repositories (spec section 8, `part start`).
import subprocess
from pathlib import Path

import pytest

from stopline.vcs.git import Git, GitError


def _git(root: Path, *arguments: str) -> str:
    identity = ["-c", "user.name=Test", "-c", "user.email=test@example.test"]
    result = subprocess.run(  # noqa: S603
        ["git", *identity, *arguments],  # noqa: S607
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    (tmp_path / "src" / "auth").mkdir(parents=True)
    (tmp_path / "other").mkdir()
    for name in ("src/auth/a.py", "src/auth/gone.py", "other/b.py"):
        (tmp_path / name).write_text("x = 1\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "first")
    return tmp_path


def test_head_is_the_current_commit(repo: Path) -> None:
    """Spec section 8: the baseline is HEAD."""
    assert Git(repo).head() == _git(repo, "rev-parse", "HEAD")


def test_clean_repository_has_no_dirty_paths(repo: Path) -> None:
    """Spec section 8: a clean tree does not block a start."""
    assert Git(repo).dirty_paths(["src/auth/**"]) == []


def test_changed_new_and_deleted_files_are_dirty(repo: Path) -> None:
    """Spec section 8: all uncommitted work in the part's paths counts, including untracked."""
    (repo / "src/auth/a.py").write_text("x = 2\n", encoding="utf-8")
    (repo / "src/auth/new.py").write_text("y = 1\n", encoding="utf-8")
    (repo / "src/auth/gone.py").unlink()

    assert sorted(Git(repo).dirty_paths(["src/auth/**"])) == [
        "src/auth/a.py",
        "src/auth/gone.py",
        "src/auth/new.py",
    ]


def test_changes_outside_the_globs_do_not_count(repo: Path) -> None:
    """Spec section 8: only the part's own paths matter."""
    (repo / "other/b.py").write_text("x = 2\n", encoding="utf-8")

    assert Git(repo).dirty_paths(["src/auth/**"]) == []


def test_renamed_file_is_listed_once_under_its_new_name(repo: Path) -> None:
    """Spec section 8: git reports a rename as two names, and only the new one is a path."""
    _git(repo, "mv", "src/auth/a.py", "src/auth/renamed.py")

    assert Git(repo).dirty_paths(["src/auth/**"]) == ["src/auth/renamed.py"]


def test_names_with_spaces_and_accents_are_not_mangled(repo: Path) -> None:
    """Spec section 8: file names come back exactly as they are on disk."""
    name = f"src/auth/a file {chr(0xE9)}.py"
    (repo / name).write_text("x = 1\n", encoding="utf-8")

    assert Git(repo).dirty_paths(["src/auth/**"]) == [name]


def test_folder_without_a_repository_is_a_git_error(tmp_path: Path) -> None:
    """Spec section 13.3: not a git repository is an environment error."""
    with pytest.raises(GitError, match="rev-parse failed"):
        Git(tmp_path).head()


def test_repository_with_no_commits_is_a_git_error(tmp_path: Path) -> None:
    """Spec section 8: there is no HEAD to record yet."""
    _git(tmp_path, "init", "-q")

    with pytest.raises(GitError, match="rev-parse failed"):
        Git(tmp_path).head()


def test_missing_git_is_a_git_error(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Spec section 13.3: git missing is an environment error."""
    monkeypatch.setenv("PATH", "")

    with pytest.raises(GitError, match="cannot be found"):
        Git(repo).head()
