# Root of the `stopline` command-line interface: --version and `schema`.
from enum import StrEnum
from typing import Annotated

import typer
from typer._click.exceptions import UsageError

from stopline.core.contract import contract_json_schema_text
from stopline.version import __version__

# Spec section 13.3: bad arguments exit 4. Typer's default of 2 means "blocking findings" here.
# Typer offers no public setting for this; tests/unit/test_cli_schema.py guards it.
UsageError.exit_code = 4

app = typer.Typer(
    name="stopline",
    help="Tells AI coding agents when a part is finished and flags unplanned features.",
    add_completion=False,
)


class SchemaName(StrEnum):
    """Schemas `stopline schema` can print. `report` arrives with the reporters in M5."""

    CONTRACT = "contract"


def _show_version(value: bool) -> None:
    """Print the version and exit when --version is given."""
    if value:
        typer.echo(f"stopline {__version__}")
        raise typer.Exit()


@app.callback()
def root(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            help="Show the version and exit.",
            callback=_show_version,
            is_eager=True,
        ),
    ] = False,
) -> None:
    """Stopline command-line interface."""


@app.command()
def schema(name: Annotated[SchemaName, typer.Argument(help="Which schema to print.")]) -> None:
    """Print a JSON Schema."""
    typer.echo(contract_json_schema_text(), nl=False)


def main() -> None:
    """Console-script entry point."""
    app()
