# The baseline service (spec section 7.1): the small part of git that Stopline needs.
import os
import shutil
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Status = Literal["added", "modified", "deleted", "renamed"]

# git's one-letter codes. A copy (C) is a new file that happens to look like an old one.
_STATUSES: dict[str, Status] = {
    "A": "added",
    "C": "added",
    "D": "deleted",
    "M": "modified",
    "T": "modified",  # changed type, such as a file that became a symbolic link
    "U": "modified",  # unresolved merge conflict
    "R": "renamed",
}


class GitError(Exception):
    """Git is missing, or this is not a repository it can use (spec section 13.3, exit 5)."""


@dataclass(frozen=True)
class ChangedFile:
    """One file that differs from the baseline. `old_path` is set only for a rename.

    Paths are relative to the top folder of the repository, with forward slashes.
    """

    path: str
    status: Status
    old_path: str | None = None


class Git:
    """Runs read-only git commands in one repository."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def root(self) -> Path:
        """Return the top folder of the repository."""
        fix = "Run this inside a git repository, or create one with `git init`."
        return Path(self._run("rev-parse", "--show-toplevel", fix=fix).strip())

    def head(self) -> str:
        """Return the id of the current commit."""
        fix = "Make a first commit with `git commit`."
        return self._run("rev-parse", "--verify", "HEAD^{commit}", fix=fix).strip()

    def user_name(self) -> str:
        """Return the name git records for you, which Stopline records as the approver."""
        fix = 'Set it with `git config user.name "Your Name"`.'
        return self._run("config", "user.name", fix=fix).strip()

    def dirty_paths(self, globs: Sequence[str]) -> list[str]:
        """Return changed and untracked files that match the globs (spec section 8, part start)."""
        pathspecs = [f":(glob){glob}" for glob in globs]
        output = self._run("status", "--porcelain", "-z", "--untracked-files=all", "--", *pathspecs)
        # Each entry is "XY path". A rename or copy is followed by one more entry, the old path.
        paths: list[str] = []
        entries = iter(output.split("\0"))
        for entry in entries:
            if entry:
                paths.append(entry[3:])
                if "R" in entry[:2] or "C" in entry[:2]:
                    next(entries)
        return paths

    def changed_files(self, baseline: str) -> list[ChangedFile]:
        """List files that differ from the baseline commit, sorted by path (spec 7.2 step 4).

        Covers committed, staged and unstaged changes, and untracked files that are not
        ignored. New files are added to a temporary copy of git's index as "intent to add",
        so git can pair a plain `mv` into a rename. That stores no file contents, and the
        real index is never changed.
        """
        top = self.root()
        index = top / self._run("rev-parse", "--git-path", "index", cwd=top).strip()
        with tempfile.TemporaryDirectory(prefix="stopline-") as folder:
            env = {"GIT_INDEX_FILE": str(Path(folder) / "index"), "GIT_LITERAL_PATHSPECS": "1"}
            if index.is_file():
                shutil.copyfile(index, env["GIT_INDEX_FILE"])
            untracked = self._call(
                ["ls-files", "-z", "--others", "--exclude-standard", "--full-name"],
                cwd=top,
                env=env,
            )
            if untracked:
                self._call(
                    ["add", "--intent-to-add", "--pathspec-from-file=-", "--pathspec-file-nul"],
                    cwd=top,
                    env=env,
                    stdin=untracked,
                )
            fix = "Check that the baseline commit still exists, for example with `git log`."
            output = self._call(
                ["diff", "-z", "--name-status", "--no-relative", "-M", baseline, "--"],
                cwd=top,
                env=env,
                fix=fix,
            )
        return sorted(_parse_name_status(output), key=lambda changed: changed.path)

    def baseline_content(self, baseline: str, path: str) -> bytes:
        """Return a file's exact bytes as they were at the baseline commit (spec section 7.1)."""
        fix = "Ask only for files that existed at the baseline: modified, deleted or renamed."
        return self._call(["cat-file", "blob", f"{baseline}:{path}"], fix=fix)

    def _run(
        self,
        *arguments: str,
        fix: str = "Check the message above from git.",
        cwd: Path | None = None,
    ) -> str:
        # A file name need not be valid UTF-8.
        return self._call(arguments, fix=fix, cwd=cwd).decode("utf-8", errors="replace")

    def _call(
        self,
        arguments: Sequence[str],
        *,
        fix: str = "Check the message above from git.",
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        stdin: bytes = b"",
    ) -> bytes:
        try:
            result = subprocess.run(  # noqa: S603
                ["git", *arguments],  # noqa: S607
                cwd=cwd or self._root,
                env={**os.environ, **env} if env else None,
                input=stdin,
                capture_output=True,
                check=True,
            )
        except FileNotFoundError:
            fix = "Install git, or add it to your PATH."
            raise GitError(f"Git cannot be found.\n  Fix: {fix}") from None
        except subprocess.CalledProcessError as err:
            message = err.stderr.decode("utf-8", errors="replace").strip()
            raise GitError(f"git {arguments[0]} failed: {message}\n  Fix: {fix}") from None
        return result.stdout


def _parse_name_status(output: bytes) -> list[ChangedFile]:
    # Each entry is a status such as "M" or "R087", then one path, or two for a rename or copy.
    fields = iter(output.decode("utf-8", errors="replace").split("\0"))
    changed: list[ChangedFile] = []
    for code in fields:
        if not code:
            continue
        status = _STATUSES[code[0]]
        if code[0] in "RC":
            old_path, path = next(fields), next(fields)
            changed.append(ChangedFile(path, status, old_path if status == "renamed" else None))
        else:
            changed.append(ChangedFile(next(fields), status))
    return changed
