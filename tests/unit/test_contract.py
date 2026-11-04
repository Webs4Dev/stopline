# Tests for loading contract files, their error messages and warnings (FR-CON-01, FR-CON-06).
import json
from pathlib import Path

import pytest

from stopline.core.contract import (
    ContractError,
    contract_json_schema_text,
    contract_warnings,
    load_contract,
)
from stopline.core.models import Status

VALID = """\
#:schema https://example.test/contract.json
schema  = 1
id      = "auth"
title   = "Authentication"
summary = "Login"
paths   = ["src/auth/**"]
status  = "draft"

[[items]]
id      = "login"
desc    = "Log in"
anchors = ["route:POST /auth/login"]

[[items]]
id        = "logout"
desc      = "Log out"
done_when = { cmd = "pytest", timeout_s = 60 }

[[approved_extras]]
feature     = "function:auth.hash"
reason      = "Helper"
approved_at = 2026-11-10T11:40:00Z
"""


def _write(tmp_path: Path, text: str, name: str = "auth.toml") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _error_for(tmp_path: Path, text: str, name: str = "auth.toml") -> ContractError:
    path = _write(tmp_path, text, name)
    with pytest.raises(ContractError) as caught:
        load_contract(path)
    return caught.value


def _line_of(text: str, snippet: str) -> int:
    """Return the 1-based line holding the snippet, so tests do not hardcode numbers."""
    return text[: text.index(snippet)].count("\n") + 1


def test_valid_contract_loads(tmp_path: Path) -> None:
    """FR-CON-01: a valid file becomes a Contract."""
    contract = load_contract(_write(tmp_path, VALID))

    assert contract.id == "auth"
    assert contract.status is Status.DRAFT
    assert [item.id for item in contract.items] == ["login", "logout"]


@pytest.mark.parametrize(
    ("old", "new", "field", "marker", "fix_hint"),
    [
        pytest.param(
            'status  = "draft"', 'status  = "done"', "status", "status  =", "git restore", id="root"
        ),
        pytest.param(
            'anchors = ["route:POST /auth/login"]',
            'anchors = ["widget:x"]',
            "items[0].anchors[0]",
            "anchors =",
            "kind:name",
            id="list element",
        ),
        pytest.param(
            "timeout_s = 60",
            "timeout_s = 901",
            "items[1].done_when.timeout_s",
            "done_when =",
            "1 to 900",
            id="second item, inline table",
        ),
        pytest.param(
            'reason      = "Helper"',
            'reason      = ""',
            "approved_extras[0].reason",
            "reason      =",
            "stopline part keep",
            id="approved extra",
        ),
    ],
)
def test_error_names_file_line_field_and_fix(
    tmp_path: Path, old: str, new: str, field: str, marker: str, fix_hint: str
) -> None:
    """FR-CON-01: errors name the file, line and field and suggest a fix."""
    text = VALID.replace(old, new)

    error = _error_for(tmp_path, text)

    assert error.field == field
    assert error.line == _line_of(text, marker)
    assert fix_hint in error.fix
    assert f"auth.toml:{error.line}: `{field}`: " in str(error)
    assert "Fix: " in str(error)


def test_error_hides_the_internal_regex(tmp_path: Path) -> None:
    """FR-CON-01: messages are for people, so the raw pattern is not shown."""
    error = _error_for(tmp_path, VALID.replace('id      = "auth"', 'id      = "Bad_ID"'))

    assert "regex" not in str(error)
    assert "allowed format" in error.problem
    assert "lower-case" in error.fix


def test_missing_root_field_points_at_first_line(tmp_path: Path) -> None:
    """FR-CON-01: a missing top-level field has no enclosing table, so line 1 is reported."""
    error = _error_for(tmp_path, VALID.replace('title   = "Authentication"\n', ""))

    assert error.field == "title"
    assert error.line == 1
    assert error.fix == "Add `title`."


def test_missing_item_field_points_at_its_table_header(tmp_path: Path) -> None:
    """FR-CON-01: a missing field falls back to the enclosing table header line."""
    text = VALID.replace('desc      = "Log out"\n', "")

    error = _error_for(tmp_path, text)

    assert error.field == "items[1].desc"
    assert error.line == _line_of(text, '[[items]]\nid        = "logout"')


def test_error_in_a_subtable_finds_the_right_line(tmp_path: Path) -> None:
    """FR-CON-01: a table written as [items.done_when] belongs to the item before it."""
    text = VALID.replace(
        'done_when = { cmd = "pytest", timeout_s = 60 }',
        '[items.done_when]\ncmd = "pytest"\ntimeout_s = 0',
    )

    error = _error_for(tmp_path, text)

    assert error.field == "items[1].done_when.timeout_s"
    assert error.line == _line_of(text, "timeout_s = 0")


def test_field_that_cannot_be_located_falls_back_to_line_1(tmp_path: Path) -> None:
    """FR-CON-01: a dotted key has no line of its own to find, so line 1 is reported."""
    text = VALID.replace('status  = "draft"', 'status  = "draft"\nbaseline.commit = "nope"')

    error = _error_for(tmp_path, text)

    assert error.field is not None
    assert error.field.startswith("baseline")
    assert error.line == 1


def test_header_with_too_many_items_points_at_the_items_table(tmp_path: Path) -> None:
    """FR-CON-01: an error about the whole list points at its first table header."""
    extra = "".join(f'\n[[items]]\nid = "extra-{n}"\ndesc = "x"\n' for n in range(11))
    text = VALID + extra

    error = _error_for(tmp_path, text)

    assert error.field == "items"
    assert error.line == _line_of(text, "[[items]]")


def test_unknown_field_suggests_the_closest_name(tmp_path: Path) -> None:
    """FR-CON-01: a misspelled field is located, and the likely name is suggested."""
    text = VALID.replace("paths   =", "pathz   =")

    error = _error_for(tmp_path, text)

    assert error.field == "pathz"
    assert error.line == _line_of(text, "pathz")
    assert "Did you mean `paths`?" in error.fix


def test_unknown_field_inside_a_table_lists_allowed_names(tmp_path: Path) -> None:
    """FR-CON-01: with no close match, the fix lists the fields allowed in that table."""
    text = VALID.replace('desc    = "Log in"', 'desc    = "Log in"\nzzz = 1')

    error = _error_for(tmp_path, text)

    assert error.field == "items[0].zzz"
    assert error.line == _line_of(text, "zzz = 1")
    assert "Allowed fields here: id, desc, anchors, done_when." in error.fix


def test_duplicate_item_ids_point_at_the_items(tmp_path: Path) -> None:
    """Spec section 9.2: repeated item ids are reported against the items."""
    text = VALID.replace('"logout"', '"login"')

    error = _error_for(tmp_path, text)

    assert error.field == "items"
    assert error.line == _line_of(text, "[[items]]")
    assert "repeated: ['login']" in error.problem


def test_id_must_match_file_name(tmp_path: Path) -> None:
    """Spec section 9.2: the id must equal the file stem."""
    error = _error_for(tmp_path, VALID, name="billing.toml")

    assert error.field == "id"
    assert error.line == _line_of(VALID, 'id      = "auth"')
    assert "'billing'" in error.fix
    assert "auth.toml" in error.fix


@pytest.mark.parametrize(
    ("text", "line"),
    [
        pytest.param('schema = 1\nid = "auth\n', 2, id="line reported by parser"),
        pytest.param('schema = 1\nid = "auth"\ntitle =', 3, id="ends mid value"),
    ],
)
def test_toml_syntax_error_gives_a_line(tmp_path: Path, text: str, line: int) -> None:
    """FR-CON-01: broken TOML is a ContractError with a line, not a crash."""
    error = _error_for(tmp_path, text)

    assert error.line == line
    assert error.problem.startswith("Invalid TOML")


def test_missing_file(tmp_path: Path) -> None:
    """FR-CON-01: a missing file says which command creates it."""
    with pytest.raises(ContractError) as caught:
        load_contract(tmp_path / "auth.toml")

    assert "does not exist" in caught.value.problem
    assert "stopline part new auth" in caught.value.fix
    assert caught.value.line is None
    assert str(caught.value).startswith(str(tmp_path / "auth.toml") + ": The contract")


def test_unreadable_path(tmp_path: Path) -> None:
    """FR-CON-01: a path that cannot be read is a ContractError, not a crash."""
    folder = tmp_path / "auth.toml"
    folder.mkdir()

    with pytest.raises(ContractError, match="cannot be read"):
        load_contract(folder)


def test_file_that_is_not_utf8(tmp_path: Path) -> None:
    """Spec section 9.1: contracts are UTF-8."""
    path = tmp_path / "auth.toml"
    path.write_bytes(b'title = "caf\xe9"\n')

    with pytest.raises(ContractError, match="not valid UTF-8"):
        load_contract(path)


def test_windows_line_endings_keep_line_numbers_right(tmp_path: Path) -> None:
    """FR-CON-01: a file saved with CRLF still reports the correct line."""
    text = VALID.replace('status  = "draft"', 'status  = "done"')
    path = tmp_path / "auth.toml"
    path.write_bytes(text.replace("\n", "\r\n").encode())

    with pytest.raises(ContractError) as caught:
        load_contract(path)

    assert caught.value.line == _line_of(text, "status  =")


def test_item_that_can_never_be_verified_gets_a_warning(tmp_path: Path) -> None:
    """FR-CON-06: an item with neither anchors nor done_when is warned about, and still loads."""
    text = VALID.replace('anchors = ["route:POST /auth/login"]\n', "")

    warnings = contract_warnings(load_contract(_write(tmp_path, text)))

    assert len(warnings) == 1
    assert "`login`" in warnings[0]
    assert "can never be verified" in warnings[0]


def test_verifiable_items_get_no_warning(tmp_path: Path) -> None:
    """FR-CON-06: an anchor or a done_when is enough."""
    assert contract_warnings(load_contract(_write(tmp_path, VALID))) == []


def test_published_schema_file_matches_the_models() -> None:
    """M1 acceptance: the published JSON Schema is the one generated from the models."""
    published = Path(__file__).parents[2] / "src/stopline/schemas/contract.v1.schema.json"

    assert published.read_text(encoding="utf-8") == contract_json_schema_text()


def test_schema_describes_a_contract() -> None:
    """M1 acceptance: the schema identifies itself and requires the contract's core fields."""
    schema = json.loads(contract_json_schema_text())

    assert schema["$id"].endswith("/src/stopline/schemas/contract.v1.schema.json")
    assert "Webs4Dev/stopline" in schema["$id"]
    required = schema["$defs"]["Contract"]["required"]
    assert set(required) == {"schema", "id", "title", "summary", "paths", "status", "items"}
