# The small part of git that starting a part needs. Milestone M2 extends this module.
import subprocess
from collections.abc import Sequence
from pathlib import Path


class GitError(Exception):
    """Git is missing, or this is not a repository it can use (spec section 13.3, exit 5)."""


class Git:
    """Runs read-only git commands in one repository."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def head(self) -> str:
        """Return the id of the current commit."""
        return self._run("rev-parse", "--verify", "HEAD^{commit}").strip()

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

    def _run(self, *arguments: str) -> str:
        try:
            result = subprocess.run(  # noqa: S603
                ["git", *arguments],  # noqa: S607
                cwd=self._root,
                capture_output=True,
                check=True,
                text=True,
                encoding="utf-8",
            )
        except FileNotFoundError:
            raise GitError("Git is not installed, or is not on your PATH.") from None
        except subprocess.CalledProcessError as err:
            raise GitError(f"git {arguments[0]} failed: {err.stderr.strip()}") from None
        return result.stdout
