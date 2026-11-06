# The `stopline part` commands (spec section 13.1). The rules live in core/; this file reads the
# arguments, prints results, and turns errors into exit codes (spec section 13.3).
import shlex
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from stopline.core.contract import (
    ContractError,
    approval_matches,
    contract_warnings,
    keep_feature,
    load_contract,
    load_with_text,
    new_part,
    part_path,
)
from stopline.core.lifecycle import (
    OwnerOnlyError,
    abandon_part,
    approve_part,
    checked_part,
    list_parts,
    pause_part,
    reopen_part,
    require_owner,
    start_part,
)
from stopline.core.models import Contract, DoneWhenCommand, Status
from stopline.vcs.git import Git, GitError

part_app = typer.Typer(help="Create parts and move them through their stages.")

PartIdArgument = Annotated[str, typer.Argument(metavar="ID", help="The part's id.")]


@contextmanager
def _friendly_errors() -> Iterator[None]:
    """Print a known problem and exit with its code, instead of showing a traceback."""
    try:
        yield
    except ContractError as err:
        typer.echo(str(err), err=True)
        raise typer.Exit(3) from None
    except OwnerOnlyError as err:
        typer.echo(str(err), err=True)
        raise typer.Exit(4) from None
    except GitError as err:
        typer.echo(str(err), err=True)
        raise typer.Exit(5) from None


def _is_terminal() -> bool:
    return sys.stdin.isatty()


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _git() -> Git:
    return Git(Path.cwd())


def _parts_dir() -> Path:
    return _git().root() / ".stopline" / "parts"


def _printable(text: str) -> str:
    """Show hidden characters as visible codes, so a contract cannot disguise what it says.

    A contract can hold terminal control codes and invisible characters, which could make the
    screen show something other than what the owner is about to approve.
    """
    return "".join(
        char if char.isprintable() else char.encode("unicode_escape").decode("ascii")
        for char in text
    )


def _command_text(done_when: list[str] | DoneWhenCommand) -> str:
    if isinstance(done_when, list):
        return shlex.join(done_when)
    return f"{done_when.cmd} (in a shell)" if done_when.shell else done_when.cmd


def _describe(contract: Contract) -> list[str]:
    """Return a contract as lines of text, including every command it would be allowed to run."""
    lines = [
        f"{contract.id}: {contract.title} [{contract.status.value}]",
        f"Summary: {contract.summary}",
        f"Paths: {', '.join(contract.paths)}",
    ]
    if contract.allow_overlap:
        lines.append("Paths may be shared with other parts (allow_overlap).")
    lines.append("Items:")
    for item in contract.items:
        lines.append(f"  {item.id}: {item.desc}")
        lines.extend(f"    anchor: {anchor}" for anchor in item.anchors)
        if item.done_when is not None:
            lines.append(f"    runs: {_command_text(item.done_when)}")
    if contract.approved_extras:
        lines.append("Approved extras:")
        lines.extend(f"  {x.feature}: {x.reason}" for x in contract.approved_extras)
    return [_printable(line) for line in lines]


def _print_warnings(contract: Contract) -> None:
    for warning in contract_warnings(contract):
        typer.echo(f"Warning: {_printable(warning)}", err=True)


@part_app.command()
def new(
    part_id: PartIdArgument,
    source: Annotated[
        Path | None,
        typer.Option("--from", help="An agent's draft to save, as TOML or JSON."),
    ] = None,
) -> None:
    """Create a draft contract, from a template or an agent's draft."""
    with _friendly_errors():
        path = new_part(part_id, _parts_dir(), source)
        _print_warnings(load_contract(path))
        typer.echo(f"Draft saved: {path}")
        typer.echo(f"Review it, then run: stopline part approve {part_id}")


@part_app.command()
def show(part_id: PartIdArgument) -> None:
    """Print a contract. Item status arrives with the checks in milestone M5."""
    with _friendly_errors():
        contract = load_contract(part_path(_parts_dir(), part_id))
        typer.echo("\n".join(_describe(contract)))
        if contract.approval is None:
            typer.echo("Approval: none")
        elif approval_matches(contract):
            approved = contract.approval
            who = _printable(approved.approved_by)
            typer.echo(f"Approval: valid, by {who} at {approved.approved_at.isoformat()}")
        else:
            typer.echo(f"Approval: NOT VALID, edited since. Run: stopline part approve {part_id}")
        _print_warnings(contract)


@part_app.command("list")
def list_command() -> None:
    """List all parts with their status and counts."""
    with _friendly_errors():
        parts = list_parts(_parts_dir())
        if not parts:
            typer.echo("No parts yet. Create one with: stopline part new ID")
        for part in parts:
            counts = f"{len(part.items)} items, {len(part.approved_extras)} extras"
            typer.echo(f"{part.id}  {part.status.value}  {counts}")


@part_app.command()
def approve(part_id: PartIdArgument) -> None:
    """Validate and sign a draft or edited contract. Owner only."""
    with _friendly_errors():
        require_owner("part approve")
        path = part_path(_parts_dir(), part_id)
        contract, reviewed = load_with_text(path)
        typer.echo("\n".join(_describe(contract)))
        _print_warnings(contract)
        if _is_terminal():
            if not typer.confirm("Approve this contract? Its `runs` commands may then run."):
                typer.echo("Not approved. Nothing changed.")
                return
            if load_with_text(path)[1] != reviewed:
                fix = f"Run `stopline part approve {part_id}` again to review the new version."
                raise ContractError(path, "The contract changed while you were reviewing it.", fix)
        approve_part(path, _git().user_name(), _now())
        typer.echo(f"Approved: {part_id}")


@part_app.command()
def start(
    part_id: PartIdArgument,
    allow_dirty: Annotated[
        bool,
        typer.Option("--allow-dirty", help="Start even with uncommitted changes in its paths."),
    ] = False,
) -> None:
    """Make a part active and record the baseline."""
    with _friendly_errors():
        start_part(part_path(_parts_dir(), part_id), _git(), _now(), allow_dirty=allow_dirty)
        typer.echo(f"Active: {part_id}")


@part_app.command()
def pause() -> None:
    """Pause the active part. Owner only."""
    with _friendly_errors():
        require_owner("part pause")
        typer.echo(f"Paused: {pause_part(_parts_dir())}")


@part_app.command()
def reopen(part_id: PartIdArgument) -> None:
    """Make a complete part active again. Owner only."""
    with _friendly_errors():
        require_owner("part reopen")
        reopen_part(part_path(_parts_dir(), part_id))
        typer.echo(f"Active: {part_id}")


@part_app.command()
def abandon(part_id: PartIdArgument) -> None:
    """Retire a part that is not complete. Owner only."""
    with _friendly_errors():
        require_owner("part abandon")
        abandon_part(part_path(_parts_dir(), part_id))
        typer.echo(f"Abandoned: {part_id}")


@part_app.command()
def keep(
    feature: Annotated[str, typer.Argument(help='The feature, such as "route:POST /login".')],
    reason: Annotated[str | None, typer.Option("--reason", help="Why it is being kept.")] = None,
) -> None:
    """Keep an unplanned feature in the active part, and re-sign the contract.

    This needs no terminal, so an agent can run it. From milestone M6 Stopline's hook makes the
    agent ask the owner's permission first (spec section 12.1).
    """
    if reason is None:
        if not _is_terminal():
            raise typer.BadParameter("is required when not at a terminal.", param_hint="--reason")
        reason = typer.prompt("Why is it being kept?")
    with _friendly_errors():
        parts_dir = _parts_dir()
        active = checked_part(parts_dir, last_completed=None)
        if active is None or active.status is not Status.ACTIVE:
            fix = "Run `stopline part start ID` or `stopline part reopen ID`, then try again."
            raise ContractError(parts_dir, "No part is active to keep this in.", fix)
        path = part_path(parts_dir, active.id)
        keep_feature(path, feature, reason, _git().user_name(), _now())
        typer.echo(f"Kept in {active.id}: {_printable(feature)}")
