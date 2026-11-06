# Tests for the contract models against the field rules in spec section 9.2.
import tomllib
from datetime import UTC, datetime

import msgspec
import pytest

from stopline.core.models import Contract, DoneWhenCommand, FeatureKey, Item, PartId, Status

# The example contract from spec section 9.1, with a full-length approval hash.
SPEC_EXAMPLE = """
schema  = 1
id      = "auth"
title   = "Authentication"
summary = "Email + password login with JWT access and refresh tokens"
paths   = ["src/auth/**", "tests/auth/**"]
status  = "approved"

[approval]
hash        = "sha256:{hash}"
approved_by = "Devansh Shah"
approved_at = 2026-11-10T09:12:00Z

[baseline]
commit     = "a1b2c3d4e5f6"
started_at = 2026-11-10T09:15:00Z

[[items]]
id        = "login"
desc      = "POST /auth/login returns access and refresh tokens"
anchors   = ["route:POST /auth/login"]
done_when = ["uv", "run", "pytest", "tests/auth/test_login.py", "-q"]

[[items]]
id        = "refresh"
desc      = "POST /auth/refresh issues a new access token"
anchors   = ["route:POST /auth/refresh", "function:auth.tokens.issue_*"]
done_when = {{ cmd = "pytest tests/auth -q && ruff check src/auth", shell = true, timeout_s = 120 }}

[[approved_extras]]
feature     = "function:auth.passwords.hash_password"
reason      = "Helper required by login"
approved_at = 2026-11-10T11:40:00Z
""".format(hash="a" * 64)


def _item(**changes: object) -> dict[str, object]:
    return {"id": "login", "desc": "Log in", **changes}


def _contract(**changes: object) -> dict[str, object]:
    return {
        "schema": 1,
        "id": "auth",
        "title": "Authentication",
        "summary": "Login",
        "paths": ["src/auth/**"],
        "status": "draft",
        "items": [_item()],
        **changes,
    }


def _numbered_items(count: int) -> list[dict[str, object]]:
    return [_item(id=f"item-{n}") for n in range(count)]


def test_spec_example_loads() -> None:
    """FR-CON-01: the example contract in spec section 9.1 is valid."""
    contract = msgspec.convert(tomllib.loads(SPEC_EXAMPLE), Contract)

    assert contract.status is Status.APPROVED
    assert contract.allow_overlap is False
    assert contract.approval is not None
    assert contract.approval.approved_at == datetime(2026, 11, 10, 9, 12, tzinfo=UTC)
    assert contract.baseline is not None
    assert contract.baseline.commit == "a1b2c3d4e5f6"
    assert contract.items[0].done_when == ["uv", "run", "pytest", "tests/auth/test_login.py", "-q"]
    assert isinstance(contract.items[1].done_when, DoneWhenCommand)
    assert contract.items[1].done_when.shell is True
    assert contract.approved_extras[0].reason == "Helper required by login"


def test_minimal_draft_defaults() -> None:
    """A new draft needs no approval, baseline or extras."""
    contract = msgspec.convert(_contract(), Contract)

    assert contract.approval is None
    assert contract.baseline is None
    assert contract.approved_extras == []
    assert contract.items[0].anchors == []
    assert contract.items[0].done_when is None


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param({"id": "a" * 48}, id="longest id"),
        pytest.param({"id": "0-part"}, id="id starts with digit"),
        pytest.param({"summary": "x" * 300}, id="longest summary"),
        pytest.param({"paths": ["p"] * 20}, id="most paths"),
        pytest.param({"items": _numbered_items(12)}, id="most items"),
        pytest.param({"items": [_item(anchors=["class:A"] * 10)]}, id="most anchors"),
        pytest.param({"items": [_item(anchors=["function:a.*_?"])]}, id="wildcards in name"),
        pytest.param({"items": [_item(anchors=["dependency:dev:ruff"])]}, id="dev dependency key"),
        pytest.param(
            {"items": [_item(done_when={"cmd": "x", "timeout_s": 900})]}, id="longest timeout"
        ),
    ],
)
def test_values_at_the_limits_are_accepted(changes: dict[str, object]) -> None:
    """FR-CON-01: values at the limits in spec section 9.2 are valid."""
    msgspec.convert(_contract(**changes), Contract)


@pytest.mark.parametrize(
    ("changes", "where"),
    [
        pytest.param({"schema": 2}, "$.schema", id="unknown schema version"),
        pytest.param({"id": "Bad_ID"}, "$.id", id="id has bad characters"),
        pytest.param({"id": "-part"}, "$.id", id="id starts with dash"),
        pytest.param({"id": "a" * 49}, "$.id", id="id too long"),
        pytest.param({"title": ""}, "$.title", id="empty title"),
        pytest.param({"summary": "x" * 301}, "$.summary", id="summary too long"),
        pytest.param({"paths": []}, "$.paths", id="no paths"),
        pytest.param({"paths": ["p"] * 21}, "$.paths", id="too many paths"),
        pytest.param({"status": "done"}, "$.status", id="unknown status"),
        pytest.param({"items": []}, "$.items", id="no items"),
        pytest.param({"items": _numbered_items(13)}, "$.items", id="too many items"),
        pytest.param(
            {"items": [_item(anchors=["class:A"] * 11)]},
            "$.items[0].anchors",
            id="too many anchors",
        ),
        pytest.param(
            {"items": [_item(anchors=["rou*:x"])]},
            "$.items[0].anchors[0]",
            id="wildcard in anchor kind",
        ),
        pytest.param(
            {"items": [_item(anchors=["widget:x"])]},
            "$.items[0].anchors[0]",
            id="unknown anchor kind",
        ),
        pytest.param(
            {"items": [_item(anchors=["login"])]},
            "$.items[0].anchors[0]",
            id="anchor without kind",
        ),
        pytest.param(
            {"items": [_item(done_when=[])]}, "$.items[0].done_when", id="empty command list"
        ),
        pytest.param(
            {"items": [_item(done_when="pytest")]}, "$.items[0].done_when", id="plain string"
        ),
        pytest.param(
            {"items": [_item(done_when={"shell": True})]},
            "$.items[0].done_when",
            id="table without cmd",
        ),
        pytest.param(
            {"items": [_item(done_when={"cmd": "x", "timeout_s": 901})]},
            "$.items[0].done_when.timeout_s",
            id="timeout too long",
        ),
        pytest.param(
            {"items": [_item(done_when={"cmd": "x", "timeout_s": 0})]},
            "$.items[0].done_when.timeout_s",
            id="timeout of zero",
        ),
        pytest.param(
            {"approved_extras": [{"feature": "class:A", "approved_at": "2026-11-10T00:00:00Z"}]},
            "$.approved_extras[0]",
            id="extra without reason",
        ),
        pytest.param(
            {
                "approval": {
                    "hash": "sha256:abc",
                    "approved_by": "D",
                    "approved_at": datetime.now(UTC),
                }
            },
            "$.approval.hash",
            id="malformed approval hash",
        ),
        pytest.param(
            {
                "baseline": {
                    "commit": "a1b2c3d",
                    "started_at": datetime(2026, 11, 10),
                }
            },
            "$.baseline.started_at",
            id="timestamp without timezone",
        ),
    ],
)
def test_invalid_values_are_rejected(changes: dict[str, object], where: str) -> None:
    """FR-CON-01: invalid values fail, and the error names the field."""
    with pytest.raises(msgspec.ValidationError) as caught:
        msgspec.convert(_contract(**changes), Contract)

    assert where in str(caught.value)


@pytest.mark.parametrize("field", ["schema", "id", "title", "summary", "paths", "status", "items"])
def test_required_fields(field: str) -> None:
    """FR-CON-01: a contract missing a required field fails and names it."""
    data = _contract()
    del data[field]

    with pytest.raises(msgspec.ValidationError, match=f"missing required field `{field}`"):
        msgspec.convert(data, Contract)


def test_unknown_field_is_rejected() -> None:
    """FR-CON-01: a misspelled field is an error, not silently ignored."""
    with pytest.raises(msgspec.ValidationError, match="unknown field `path`"):
        msgspec.convert(_contract(path=["src/**"]), Contract)


def test_item_ids_must_be_unique() -> None:
    """Spec section 9.2: item id is unique within the part."""
    with pytest.raises(msgspec.ValidationError, match=r"repeated: \['login'\]"):
        msgspec.convert(_contract(items=[_item(), _item()]), Contract)


def test_done_when_table_defaults_to_no_shell() -> None:
    """Spec section 9.2: `shell = true` must be explicit; default timeout is 120 s."""
    item = msgspec.convert(_item(done_when={"cmd": "pytest"}), Item)

    assert item.done_when == DoneWhenCommand(cmd="pytest", shell=False, timeout_s=120, cwd=None)


def test_contracts_cannot_be_changed_after_loading() -> None:
    """Lifecycle changes must go through Stopline, so loaded contracts are read-only."""
    contract = msgspec.convert(_contract(), Contract)

    with pytest.raises(AttributeError):
        contract.status = Status.ACTIVE  # type: ignore[misc]


def test_contract_can_generate_a_json_schema() -> None:
    """FR-CON-01: the model can produce the JSON Schema published in the next step."""
    schema = msgspec.json.schema(Contract)

    assert schema["$ref"] == "#/$defs/Contract"


@pytest.mark.parametrize(
    ("value", "kind"),
    [
        pytest.param("auth\n", PartId, id="part id"),
        pytest.param("function:x\n", FeatureKey, id="feature key"),
    ],
)
def test_trailing_line_break_is_rejected(value: str, kind: object) -> None:
    """Spec section 9.2: Python's $ accepts a final line break, which these patterns must not."""
    with pytest.raises(msgspec.ValidationError):
        msgspec.convert(value, kind)
