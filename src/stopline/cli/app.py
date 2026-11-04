# Root of the `stopline` command-line interface. M0: only --version.
from typing import Annotated

import typer

from stopline.version import __version__

app = typer.Typer(
    name="stopline",
    help="Tells AI coding agents when a part is finished and flags unplanned features.",
    add_completion=False,
)


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


def main() -> None:
    """Console-script entry point."""
    app()
