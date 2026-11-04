"""M0 acceptance: `stopline --version` works (spec section 23.1, M0)."""

from typer.testing import CliRunner

from stopline import __version__
from stopline.cli.app import app

runner = CliRunner()


def test_m0_version_flag_prints_version() -> None:
    """M0: `stopline --version` prints the package version and exits 0."""
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == f"stopline {__version__}"
