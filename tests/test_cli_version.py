"""M0 acceptance: `stopline --version` works (spec section 23.1, M0)."""

import runpy
import sys

import pytest
from typer.testing import CliRunner

from stopline import __version__
from stopline.cli.app import app, main

runner = CliRunner()


def test_m0_version_flag_prints_version() -> None:
    """M0: `stopline --version` prints the package version and exits 0."""
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == f"stopline {__version__}"


def test_m0_console_script_entry_point(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """M0: the `main()` console-script entry point prints the version and exits 0."""
    monkeypatch.setattr(sys, "argv", ["stopline", "--version"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"stopline {__version__}"


def test_m0_python_dash_m_entry_point(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """M0: `python -m stopline --version` prints the version and exits 0."""
    monkeypatch.setattr(sys, "argv", ["stopline", "--version"])
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("stopline", run_name="__main__")
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"stopline {__version__}"
