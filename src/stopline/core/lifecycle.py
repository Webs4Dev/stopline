# The states a part moves through and the rules for each move (spec section 8), plus the check
# that two parts do not share paths (spec section 9.2). tomlkit is imported only when writing.
import os
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Protocol

from stopline.core.contract import (
    ContractError,
    add_table,
    approval_matches,
    load_contract,
    load_with_text,
    save_document,
    sign_document,
)
from stopline.core.models import Contract, Status

_WILDCARDS = frozenset("*?[")


class OwnerOnlyError(Exception):
    """An owner-only command was run where no person can be at the keyboard (FR-LC-03)."""


class GitState(Protocol):
    """What starting a part needs to know from git."""

    def head(self) -> str:
        """Return the id of the current commit."""
        ...

    def dirty_paths(self, globs: Sequence[str]) -> list[str]:
        """Return changed and untracked files that match the globs."""
        ...


def require_owner(command: str) -> None:
    """Refuse to continue without an interactive terminal (FR-LC-03)."""
    if not sys.stdin.isatty() and os.environ.get("STOPLINE_ALLOW_NONINTERACTIVE") != "1":
        raise OwnerOnlyError(
            f"`stopline {command}` can only be run by the owner, from an interactive terminal.\n"
            "  Fix: Run it yourself in a terminal. Scripts you control can set "
            "STOPLINE_ALLOW_NONINTERACTIVE=1."
        )


def list_parts(parts_dir: Path) -> list[Contract]:
    """Return every contract in the folder, sorted by file name."""
    return [load_contract(path) for path in sorted(parts_dir.glob("*.toml"))]


def checked_part(parts_dir: Path, last_completed: str | None) -> Contract | None:
    """Return the part checks run against: the active one, else the last completed (FR-LC-05).

    Contracts do not record when a part completed, so the caller says which was last. From M2
    that comes from state.db; see docs/open-questions.md.
    """
    parts = list_parts(parts_dir)
    active = [part for part in parts if part.status is Status.ACTIVE]
    if active:
        return active[0]
    for part in parts:
        if part.status is Status.COMPLETE and part.id == last_completed:
            return part
    return None


def approve_part(path: Path, approved_by: str, now: datetime) -> None:
    """Sign a draft, or re-sign an edited part while keeping its status and baseline (FR-LC-06)."""
    contract, text = load_with_text(path)
    if contract.status is Status.ABANDONED:
        fix = "Abandoned parts are kept for history. Create a new part instead."
        raise ContractError(path, f"Part `{contract.id}` is abandoned.", fix, field="status")
    _refuse_shared_paths(path, contract)

    status = Status.APPROVED if contract.status is Status.DRAFT else contract.status
    _rewrite(path, text, status, signed=(approved_by, now))


def start_part(path: Path, git: GitState, now: datetime, *, allow_dirty: bool = False) -> None:
    """Make an approved part active. Only the first start records the baseline (spec section 8)."""
    contract, text = load_with_text(path)
    if contract.status is not Status.APPROVED:
        fixes = {
            Status.DRAFT: f"Run `stopline part approve {contract.id}` first.",
            Status.ACTIVE: "There is nothing to do.",
            Status.COMPLETE: f"Run `stopline part reopen {contract.id}` to work on it again.",
            Status.ABANDONED: "Abandoned parts are kept for history. Create a new part instead.",
        }
        problem = f"Part `{contract.id}` is {contract.status.value}, so it cannot be started."
        raise ContractError(path, problem, fixes[contract.status], field="status")
    if not approval_matches(contract):
        problem = f"Part `{contract.id}` changed since it was approved."
        fix = f"Review the contract, then run `stopline part approve {contract.id}`."
        raise ContractError(path, problem, fix, field="approval")
    _refuse_second_active(path, contract)
    if not allow_dirty:
        dirty = git.dirty_paths(contract.paths)
        if dirty:
            problem = f"Uncommitted changes in this part's paths: {', '.join(dirty)}."
            fix = "Commit or stash them, or pass --allow-dirty to count them as part of this part."
            raise ContractError(path, problem, fix)

    baseline = None
    if contract.baseline is None:
        baseline = {"commit": git.head(), "started_at": now}
    _rewrite(path, text, Status.ACTIVE, baseline=baseline)


def pause_part(parts_dir: Path) -> str:
    """Move the active part back to approved, keeping its baseline, and return its id."""
    for path in sorted(parts_dir.glob("*.toml")):
        contract, text = load_with_text(path)
        if contract.status is Status.ACTIVE:
            _rewrite(path, text, Status.APPROVED)
            return contract.id
    fix = "There is nothing to pause."
    raise ContractError(parts_dir, "No part is active.", fix)


def complete_part(path: Path) -> None:
    """Mark an active part complete. Completing a complete part changes nothing (FR-LC-04)."""
    contract, text = load_with_text(path)
    if contract.status is Status.COMPLETE:
        return
    if contract.status is not Status.ACTIVE:
        problem = f"Part `{contract.id}` is {contract.status.value}, so it cannot complete."
        fix = f"Run `stopline part start {contract.id}` first."
        raise ContractError(path, problem, fix, field="status")
    if not approval_matches(contract):
        problem = f"Part `{contract.id}` changed since it was approved, so it cannot complete."
        fix = f"Review the contract, then run `stopline part approve {contract.id}`."
        raise ContractError(path, problem, fix, field="approval")
    _rewrite(path, text, Status.COMPLETE)


def reopen_part(path: Path) -> None:
    """Move a complete part back to active. Edited items then need `part approve` (spec 8)."""
    contract, text = load_with_text(path)
    if contract.status is not Status.COMPLETE:
        problem = f"Part `{contract.id}` is {contract.status.value}; only a complete part reopens."
        raise ContractError(path, problem, "There is nothing to reopen.", field="status")
    _refuse_second_active(path, contract)
    _rewrite(path, text, Status.ACTIVE)


def abandon_part(path: Path) -> None:
    """Retire any part that is not complete. It stays on disk for history (spec section 8)."""
    contract, text = load_with_text(path)
    if contract.status in (Status.COMPLETE, Status.ABANDONED):
        problem = f"Part `{contract.id}` is {contract.status.value}, so it cannot be abandoned."
        raise ContractError(path, problem, "There is nothing to do.", field="status")
    _rewrite(path, text, Status.ABANDONED)


def globs_overlap(first: str, second: str) -> bool:
    """Return whether two globs are identical or one sits inside the other.

    This does not catch every overlap: `src/*.py` and `src/a*` are not reported, because
    deciding that in general needs far more code than the check is worth.
    """
    first, second = _normalise(first), _normalise(second)
    return first == second or _contains(first, second) or _contains(second, first)


def _normalise(glob: str) -> str:
    return glob.removeprefix("./").lstrip("/")


def _contains(outer: str, inner: str) -> bool:
    """Return whether `outer` names a whole folder that `inner` lies inside."""
    if outer == "**":
        return True
    if outer.endswith("/**"):
        root = outer[:-3]
    elif _WILDCARDS.isdisjoint(outer):  # a plain name can be a folder, as in .gitignore
        root = outer.rstrip("/")
    else:
        return False
    return inner == root or inner.startswith(f"{root}/")


def _refuse_shared_paths(path: Path, contract: Contract) -> None:
    """Spec section 9.2: parts share paths only if both set allow_overlap."""
    for other in list_parts(path.parent):
        if other.id == contract.id or other.status is Status.ABANDONED:
            continue
        if contract.allow_overlap and other.allow_overlap:
            continue
        for mine in contract.paths:
            for theirs in other.paths:
                if globs_overlap(mine, theirs):
                    problem = f"`{mine}` overlaps `{theirs}` in part `{other.id}`."
                    fix = "Narrow the paths, or set `allow_overlap = true` on both parts."
                    raise ContractError(path, problem, fix, field="paths")


def _refuse_second_active(path: Path, contract: Contract) -> None:
    """FR-LC-02: at most one active part per worktree."""
    for other in list_parts(path.parent):
        if other.status is Status.ACTIVE and other.id != contract.id:
            problem = f"Part `{other.id}` is already active, and only one can be active at a time."
            fix = f"Ask the owner to run `stopline part pause` before working on `{contract.id}`."
            raise ContractError(path, problem, fix, field="status")


def _rewrite(
    path: Path,
    text: str,
    status: Status,
    *,
    baseline: Mapping[str, object] | None = None,
    signed: tuple[str, datetime] | None = None,
) -> None:
    """Change the managed fields in the contract's text, keeping everything else."""
    import tomlkit

    document = tomlkit.parse(text)
    document["status"] = status.value
    if baseline is not None:
        add_table(document, "baseline", baseline)
    if signed is not None:
        sign_document(document, path, *signed)
    save_document(document, path)
