# Tests for the approval hash on real contract files (FR-CON-02, FR-CON-03).
import hashlib
from datetime import UTC, datetime
from pathlib import Path

import msgspec
import pytest

from stopline.core.contract import approval_hash, approval_matches, load_contract
from stopline.core.models import Approval, Contract, Item, Status

BASE = """\
schema  = 1
id      = "auth"
title   = "Authentication"
summary = "Login  and logout"
paths   = ["src/auth/**", "tests/auth/**"]
status  = "draft"

[[items]]
id        = "login"
desc      = "Log in"
anchors   = ["route:POST /auth/login"]
done_when = ["pytest", "-k", "a  b"]

[[items]]
id        = "logout"
desc      = "Log out"
done_when = { cmd = "pytest" }

[[approved_extras]]
feature     = "function:auth.hash"
reason      = "Helper"
approved_at = 2026-11-10T11:40:00Z
"""


def _load(tmp_path: Path, text: str) -> Contract:
    path = tmp_path / "auth.toml"
    path.write_bytes(text.encode("utf-8"))
    return load_contract(path)


def _minimal_contract() -> Contract:
    return Contract(
        schema=1,
        id="auth",
        title="Authentication",
        summary="Log  in",
        paths=["src/**"],
        status=Status.DRAFT,
        items=[Item(id="login", desc="Log in")],
    )


def test_hash_of_a_known_contract_never_changes() -> None:
    """FR-CON-02: the exact bytes are pinned, so a change here would break every approval."""
    canonical = (
        '{"approved_extras":[],'
        '"items":[{"anchors":[],"desc":"Log in","done_when":null,"id":"login"}],'
        '"paths":["src/**"],'
        '"summary":"Log  in"}'
    )
    expected = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    assert approval_hash(_minimal_contract()) == expected


def test_hash_fits_the_approval_field() -> None:
    """FR-CON-02: the result is accepted by the `approval.hash` rule in the model."""
    approval = {"hash": approval_hash(_minimal_contract()), "approved_by": "a"}

    msgspec.convert({**approval, "approved_at": "2026-11-10T09:12:00Z"}, Approval)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        pytest.param('summary = "Login', '# A comment\nsummary = "Login', id="comment added"),
        pytest.param('paths   = ["', 'paths = [ "', id="spacing around the value"),
        pytest.param(
            'desc      = "Log in"\nanchors   = ["route:POST /auth/login"]',
            'anchors   = ["route:POST /auth/login"]\ndesc      = "Log in"',
            id="keys reordered",
        ),
        pytest.param('{ cmd = "pytest" }', '{ timeout_s = 120, cmd = "pytest" }', id="default set"),
        pytest.param('{ cmd = "pytest" }', '{ cmd = "pytest", shell = false }', id="shell default"),
        pytest.param('id        = "logout"', 'id        = "logout"\nanchors = []', id="empty list"),
        pytest.param("2026-11-10T11:40:00Z", "2026-11-10T11:40:00+00:00", id="same instant"),
        pytest.param('title   = "Authentication"', 'title   = "Auth"', id="title not covered"),
        pytest.param('status  = "draft"', 'status  = "active"', id="status not covered"),
    ],
)
def test_changes_that_keep_the_hash(tmp_path: Path, old: str, new: str) -> None:
    """FR-CON-02: layout and fields outside summary, paths, items and extras do not count."""
    assert old in BASE

    assert approval_hash(_load(tmp_path, BASE.replace(old, new))) == approval_hash(
        _load(tmp_path, BASE)
    )


@pytest.mark.parametrize(
    ("old", "new"),
    [
        pytest.param('"Login  and logout"', '"Login and logout"', id="summary double space"),
        pytest.param('"Login  and logout"', '"Login  and logout "', id="summary trailing space"),
        pytest.param('"a  b"', '"a b"', id="double space inside a command"),
        pytest.param('["pytest", "-k",', '["-k", "pytest",', id="argument order"),
        pytest.param(
            '["src/auth/**", "tests/auth/**"]', '["tests/auth/**", "src/auth/**"]', id="path order"
        ),
        pytest.param('"src/auth/**"', '"src/**"', id="wider path"),
        pytest.param('desc      = "Log in"', 'desc      = "Log in!"', id="item description"),
        pytest.param('{ cmd = "pytest" }', '{ cmd = "pytest", shell = true }', id="shell on"),
        pytest.param('{ cmd = "pytest" }', '{ cmd = "pytest", timeout_s = 900 }', id="timeout"),
        pytest.param('reason      = "Helper"', 'reason      = "Help"', id="extra reason"),
        pytest.param("2026-11-10T11:40:00Z", "2026-11-10T11:40:01Z", id="extra time"),
    ],
)
def test_changes_that_break_the_hash(tmp_path: Path, old: str, new: str) -> None:
    """FR-CON-02: values are hashed exactly as written, so any real edit shows up."""
    assert old in BASE

    assert approval_hash(_load(tmp_path, BASE.replace(old, new))) != approval_hash(
        _load(tmp_path, BASE)
    )


def test_windows_line_endings_keep_the_hash(tmp_path: Path) -> None:
    """FR-CON-02: git can rewrite line endings on Windows, which must not break an approval."""
    text = BASE.replace('"Login  and logout"', '"""Login\nand logout"""')

    assert approval_hash(_load(tmp_path, text.replace("\n", "\r\n"))) == approval_hash(
        _load(tmp_path, text)
    )


def test_approval_matches_when_the_contract_is_unchanged(tmp_path: Path) -> None:
    """FR-CON-03: a contract whose stored hash equals its content counts as approved."""
    current = approval_hash(_load(tmp_path, BASE))
    signed = (
        f'{BASE}\n[approval]\nhash = "{current}"\n'
        'approved_by = "Devansh Shah"\napproved_at = 2026-11-10T09:12:00Z\n'
    )

    assert approval_matches(_load(tmp_path, signed))


def test_approval_fails_after_an_edit(tmp_path: Path) -> None:
    """FR-CON-03: editing a signed contract makes it unapproved."""
    current = approval_hash(_load(tmp_path, BASE))
    signed = (
        f'{BASE}\n[approval]\nhash = "{current}"\n'
        'approved_by = "Devansh Shah"\napproved_at = 2026-11-10T09:12:00Z\n'
    )

    assert not approval_matches(_load(tmp_path, signed.replace('"a  b"', '"a b"')))


def test_contract_without_an_approval_does_not_match() -> None:
    """FR-CON-03: a draft that was never signed is not approved."""
    assert not approval_matches(_minimal_contract())


def test_approval_time_and_signer_are_not_part_of_the_check() -> None:
    """FR-CON-08: who signed and when are guarded by the trust record, not by this hash."""
    contract = _minimal_contract()
    approval = Approval(
        hash=approval_hash(contract),
        approved_by="Someone Else",
        approved_at=datetime(2030, 1, 1, tzinfo=UTC),
    )

    assert approval_matches(msgspec.structs.replace(contract, approval=approval))
