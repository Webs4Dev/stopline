# Tests for the git helper against real repositories (spec section 8, `part start`).
import os
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


# The baseline service (spec 7.1, 7.2 step 4). M2 acceptance: fixture repos with
# untracked files and renames.

BIG = "".join(f"line {number}\n" for number in range(40))


def _changes(repo: Path, baseline: str) -> list[tuple[str, str, str | None]]:
    return [(c.path, c.status, c.old_path) for c in Git(repo).changed_files(baseline)]


def test_clean_tree_has_no_changed_files(repo: Path) -> None:
    """Spec 7.2 step 4: nothing changed since the baseline."""
    assert Git(repo).changed_files(Git(repo).head()) == []


def test_each_kind_of_change_is_listed(repo: Path) -> None:
    """Spec 7.2 step 4: modified, deleted, untracked and staged new files, sorted by path."""
    baseline = Git(repo).head()
    (repo / "src/auth/a.py").write_text("x = 2\n", encoding="utf-8")
    (repo / "src/auth/gone.py").unlink()
    (repo / "src/auth/untracked.py").write_text("y = 1\n", encoding="utf-8")
    (repo / "src/auth/staged.py").write_text("z = 1\n", encoding="utf-8")
    _git(repo, "add", "src/auth/staged.py")

    assert _changes(repo, baseline) == [
        ("src/auth/a.py", "modified", None),
        ("src/auth/gone.py", "deleted", None),
        ("src/auth/staged.py", "added", None),
        ("src/auth/untracked.py", "added", None),
    ]


def test_commits_made_after_the_baseline_count(repo: Path) -> None:
    """Spec 7.2 step 4: the baseline is a commit, so later commits are part of the change."""
    baseline = Git(repo).head()
    (repo / "src/auth/a.py").write_text("x = 2\n", encoding="utf-8")
    (repo / "src/auth/later.py").write_text("y = 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "second")

    assert _changes(repo, baseline) == [
        ("src/auth/a.py", "modified", None),
        ("src/auth/later.py", "added", None),
    ]


def test_ignored_files_are_not_listed(repo: Path) -> None:
    """Spec 7.2 step 4: untracked files count only if git does not ignore them."""
    baseline = Git(repo).head()
    (repo / ".git/info/exclude").write_text("*.log\n", encoding="utf-8")
    (repo / "debug.log").write_text("noise\n", encoding="utf-8")

    assert _changes(repo, baseline) == []


@pytest.mark.parametrize("stage", [False, True], ids=["plain-mv", "git-mv"])
def test_moved_file_is_a_rename(repo: Path, stage: bool) -> None:
    """Spec 7.2 step 4: a move is a rename even if git was never told about it."""
    (repo / "src/auth/big.py").write_text(BIG, encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "big")
    baseline = Git(repo).head()
    if stage:
        _git(repo, "mv", "src/auth/big.py", "src/moved.py")
    else:
        (repo / "src/auth/big.py").rename(repo / "src/moved.py")

    assert _changes(repo, baseline) == [("src/moved.py", "renamed", "src/auth/big.py")]


def test_moved_and_edited_file_is_still_a_rename(repo: Path) -> None:
    """Spec 7.2 step 4: a small edit during a move keeps the pairing (git's 50% rule)."""
    (repo / "src/auth/big.py").write_text(BIG, encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "big")
    baseline = Git(repo).head()
    (repo / "src/auth/big.py").unlink()
    (repo / "src/moved.py").write_text(BIG + "extra\n", encoding="utf-8")

    assert _changes(repo, baseline) == [("src/moved.py", "renamed", "src/auth/big.py")]


def test_unrelated_delete_and_new_file_are_not_paired(repo: Path) -> None:
    """Spec 7.2 step 4: different contents stay a deletion and an addition."""
    baseline = Git(repo).head()
    (repo / "src/auth/gone.py").unlink()
    (repo / "src/auth/fresh.py").write_text(BIG, encoding="utf-8")

    assert _changes(repo, baseline) == [
        ("src/auth/fresh.py", "added", None),
        ("src/auth/gone.py", "deleted", None),
    ]


def test_unusual_names_come_back_exactly(repo: Path) -> None:
    """Spec 7.2 step 4: spaces, accents and glob characters are plain names, not patterns."""
    baseline = Git(repo).head()
    names = [f"src/a file {chr(0xE9)}.py", "src/[x].py", "src/-dash.py"]
    for name in names:
        (repo / name).write_text("x = 1\n", encoding="utf-8")

    assert _changes(repo, baseline) == [(name, "added", None) for name in sorted(names)]


def test_paths_are_from_the_top_even_inside_a_subfolder(repo: Path) -> None:
    """Spec 7.2 step 4: the answer does not depend on where the command runs."""
    baseline = Git(repo).head()
    (repo / "src/auth/a.py").write_text("x = 2\n", encoding="utf-8")
    (repo / "other/new.py").write_text("y = 1\n", encoding="utf-8")

    assert _changes(repo / "src/auth", baseline) == [
        ("other/new.py", "added", None),
        ("src/auth/a.py", "modified", None),
    ]


def test_real_index_and_object_store_are_untouched(repo: Path) -> None:
    """NFR-08: listing changes stores no file contents and leaves the owner's staging alone."""
    baseline = Git(repo).head()
    (repo / "src/auth/a.py").write_text("x = 2\n", encoding="utf-8")
    (repo / "src/auth/private.txt").write_text("not for git\n", encoding="utf-8")
    index_before = (repo / ".git/index").read_bytes()
    objects_before = sorted((repo / ".git/objects").rglob("*"))

    Git(repo).changed_files(baseline)

    assert (repo / ".git/index").read_bytes() == index_before
    assert _git(repo, "status", "--porcelain") == "M src/auth/a.py\n?? src/auth/private.txt"
    new_objects = sorted(set((repo / ".git/objects").rglob("*")) - set(objects_before))
    # git's shared empty-file object marks "will be added later"; nothing else may appear.
    assert [p.parent.name + p.name for p in new_objects if p.is_file()] in (
        [],
        ["e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"],
    )


def test_unknown_baseline_is_a_git_error(repo: Path) -> None:
    """Spec 13.3: a baseline commit that no longer exists is an environment error."""
    with pytest.raises(GitError, match="baseline commit still exists"):
        Git(repo).changed_files("0" * 40)


def test_baseline_content_is_the_old_bytes(repo: Path) -> None:
    """Spec 7.1: old contents come from git, byte for byte, even after the file changed."""
    data = bytes(range(256))
    (repo / "src/auth/blob.bin").write_bytes(data)
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "binary")
    baseline = Git(repo).head()
    (repo / "src/auth/blob.bin").write_bytes(b"new")
    (repo / "src/auth/a.py").unlink()

    assert Git(repo).baseline_content(baseline, "src/auth/blob.bin") == data
    assert Git(repo / "other").baseline_content(baseline, "src/auth/a.py") == b"x = 1\n"


def test_baseline_content_of_a_new_file_is_a_git_error(repo: Path) -> None:
    """Spec 7.1: an added file has no baseline version."""
    baseline = Git(repo).head()
    (repo / "src/auth/new.py").write_text("y = 1\n", encoding="utf-8")

    with pytest.raises(GitError, match="existed at the baseline"):
        Git(repo).baseline_content(baseline, "src/auth/new.py")


# Secret files (NFR-08) and the tree hash (spec 15.2).

SECRET_NAMES = [".env", "config/.ENV.local", "deploy/server.pem", "credentials.json"]


def _write_secrets(repo: Path) -> None:
    for name in SECRET_NAMES:
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(f"value of {name}\n", encoding="utf-8")


def _stored(repo: Path, name: str) -> bool:
    """Whether git's object store holds this file's current contents."""
    sha = _git(repo, "hash-object", "--", name)  # without -w: computes, stores nothing
    command = ["git", "cat-file", "-e", sha]
    exists = subprocess.run(command, cwd=repo, check=False)  # noqa: S603
    return exists.returncode == 0


def test_secret_files_are_never_listed(repo: Path) -> None:
    """NFR-08: untracked and tracked secret files stay out of the changed files."""
    (repo / "keys").mkdir()
    (repo / "keys/id_rsa").write_text("old\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "a committed key")
    baseline = Git(repo).head()
    (repo / "keys/id_rsa").write_text("new\n", encoding="utf-8")
    _write_secrets(repo)

    assert _changes(repo, baseline) == []


def test_clean_tree_hash_is_the_commit_tree(repo: Path) -> None:
    """Spec 15.2: with nothing changed, the hash is exactly the tree git already has."""
    assert Git(repo).tree_hash() == _git(repo, "rev-parse", "HEAD^{tree}")


def test_tree_hash_changes_with_every_kind_of_change(repo: Path) -> None:
    """Spec 15.2: edits, new untracked files and deletions all give a new hash."""
    seen = {Git(repo).tree_hash()}
    (repo / "src/auth/a.py").write_text("x = 2\n", encoding="utf-8")
    seen.add(Git(repo).tree_hash())
    (repo / "src/auth/untracked.py").write_text("y = 1\n", encoding="utf-8")
    seen.add(Git(repo).tree_hash())
    (repo / "other/b.py").unlink()
    seen.add(Git(repo).tree_hash())

    assert len(seen) == 4


def test_tree_hash_is_stable_and_the_same_from_a_subfolder(repo: Path) -> None:
    """Spec 15.2: an unchanged tree gives the same hash wherever it is asked."""
    (repo / "src/auth/untracked.py").write_text("y = 1\n", encoding="utf-8")

    assert Git(repo).tree_hash() == Git(repo / "src/auth").tree_hash() == Git(repo).tree_hash()


def test_tree_hash_ignores_stopline_ignored_and_secret_files(repo: Path) -> None:
    """Spec 15.2 and NFR-08: Stopline's own files, ignored files and secrets do not count."""
    before = Git(repo).tree_hash()
    (repo / ".stopline/parts").mkdir(parents=True)
    (repo / ".stopline/parts/p.toml").write_text("id = 'p'\n", encoding="utf-8")
    (repo / ".git/info/exclude").write_text("*.log\n", encoding="utf-8")
    (repo / "debug.log").write_text("noise\n", encoding="utf-8")
    _write_secrets(repo)

    assert Git(repo).tree_hash() == before


def test_tracked_stopline_and_secret_files_are_left_out_of_the_tree_hash(repo: Path) -> None:
    """Spec 15.2: committed contracts and committed secrets are excluded too."""
    clean = Git(repo).tree_hash()
    (repo / ".stopline").mkdir()
    (repo / ".stopline/x.toml").write_text("id = 'x'\n", encoding="utf-8")
    (repo / ".env").write_text("A=1\n", encoding="utf-8")
    _git(repo, "add", "-f", ".")
    _git(repo, "commit", "-q", "-m", "tracked extras")

    assert Git(repo).tree_hash() == clean


def test_tree_hash_never_stores_secrets_or_touches_the_index(repo: Path) -> None:
    """NFR-08: secrets are not copied into git; the owner's staging is unchanged."""
    _write_secrets(repo)
    (repo / "src/auth/new.py").write_text("y = 1\n", encoding="utf-8")
    index_before = (repo / ".git/index").read_bytes()

    Git(repo).tree_hash()

    assert (repo / ".git/index").read_bytes() == index_before
    assert _stored(repo, "src/auth/new.py")  # ordinary files are stored, as write-tree needs
    assert not any(_stored(repo, name) for name in SECRET_NAMES)


def test_edit_that_keeps_size_and_timestamp_is_still_seen(repo: Path) -> None:
    """Spec 7.2 step 4 and 15.2: a same-size edit with an unchanged timestamp still counts.

    Git spots such "racily clean" files by comparing them with the index file's own
    timestamp, so the scratch copy of the index must keep that timestamp. A coarse clock,
    as on Windows, makes this happen for real; here the timestamps are set by hand.
    """
    _git(repo, "config", "core.trustctime", "false")  # as on Windows, which has no ctime
    target, index = repo / "src/auth/a.py", repo / ".git/index"
    old = 1_577_836_800  # 2020-01-01, long before the index is written
    os.utime(target, (old, old))
    _git(repo, "update-index", "--refresh")  # the index now records the old timestamp
    baseline, clean_tree = Git(repo).head(), Git(repo).tree_hash()
    target.write_text("x = 2\n", encoding="utf-8")  # same size as "x = 1\n"
    os.utime(target, (old, old))
    os.utime(index, (old, old))

    assert _changes(repo, baseline) == [("src/auth/a.py", "modified", None)]
    assert Git(repo).tree_hash() != clean_tree
