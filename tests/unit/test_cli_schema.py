# Tests for `stopline schema` and the usage-error exit code (spec sections 13.1 and 13.3).
import json

import pytest
from typer.testing import CliRunner

from stopline.cli.app import app
from stopline.core.contract import contract_json_schema_text

runner = CliRunner()


def test_schema_contract_prints_the_schema() -> None:
    """Spec section 13.1: `stopline schema contract` prints the JSON Schema."""
    result = runner.invoke(app, ["schema", "contract"])

    assert result.exit_code == 0
    assert result.output == contract_json_schema_text()
    assert json.loads(result.output)["$ref"] == "#/$defs/Contract"


@pytest.mark.parametrize(
    "arguments",
    [
        pytest.param(["schema", "nonsense"], id="bad value"),
        pytest.param(["schema"], id="missing argument"),
        pytest.param(["nonsense"], id="unknown command"),
        pytest.param(["--nonsense"], id="unknown option"),
    ],
)
def test_usage_errors_exit_4(arguments: list[str]) -> None:
    """Spec section 13.3: bad arguments exit 4, because 2 means blocking findings."""
    assert runner.invoke(app, arguments).exit_code == 4
