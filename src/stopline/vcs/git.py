# The small part of git that the part commands need. Milestone M2 extends this module.
import subprocess
from collections.abc import Sequence
from pathlib import Path


class GitError(Exception):
    """Git is missing, or this is not a repository it can use (spec section 13.3, exit 5)."""


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

    def _run(self, *arguments: str, fix: str = "Check the message above from git.") -> str:
        try:
            result = subprocess.run(  # noqa: S603
                ["git", *arguments],  # noqa: S607
                cwd=self._root,
                capture_output=True,
                check=True,
                text=True,
                encoding="utf-8",
                errors="replace",  # a file name need not be valid UTF-8
            )
        except FileNotFoundError:
            fix = "Install git, or add it to your PATH."
            raise GitError(f"Git cannot be found.\n  Fix: {fix}") from None
        except subprocess.CalledProcessError as err:
            problem = f"git {arguments[0]} failed: {err.stderr.strip()}"
            raise GitError(f"{problem}\n  Fix: {fix}") from None
        return result.stdout
