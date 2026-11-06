# Randomised tests for the approval hash on generated contracts (FR-CON-02, spec section 20.1).
from collections.abc import Callable
from datetime import UTC, datetime

import msgspec
import pytest
from hypothesis import given
from hypothesis import strategies as st

from stopline.core.contract import approval_hash
from stopline.core.models import ApprovedExtra, Contract, DoneWhenCommand, Item, Status

_text = st.text(min_size=1, max_size=30)
_feature = st.from_regex(r"\A(route|function|table):[a-zA-Z0-9_. /*?]{1,20}\Z")
_done_when = (
    st.none()
    | st.lists(_text, min_size=1, max_size=4)
    | st.builds(
        DoneWhenCommand,
        cmd=_text,
        shell=st.booleans(),
        timeout_s=st.integers(1, 900),
        cwd=st.none() | _text,
    )
)
_item = st.builds(
    Item,
    id=_text,
    desc=_text,
    anchors=st.lists(_feature, max_size=3),
    done_when=_done_when,
)
_extra = st.builds(
    ApprovedExtra,
    feature=_feature,
    reason=_text,
    approved_at=st.datetimes(timezones=st.just(UTC)),
)
_contracts = st.builds(
    Contract,
    schema=st.just(1),
    id=st.just("auth"),
    title=_text,
    summary=_text,
    paths=st.lists(_text, min_size=1, max_size=4),
    status=st.sampled_from(Status),
    items=st.lists(_item, min_size=1, max_size=4, unique_by=lambda item: item.id),
    approved_extras=st.lists(_extra, max_size=3),
)

Edit = Callable[[Contract], Contract]


def _replace(contract: Contract, **changes: object) -> Contract:
    return msgspec.structs.replace(contract, **changes)


def _with_first_item(contract: Contract, **changes: object) -> Contract:
    first = msgspec.structs.replace(contract.items[0], **changes)
    return _replace(contract, items=[first, *contract.items[1:]])


def _with_keys_reversed(value: object) -> object:
    if isinstance(value, dict):
        return {key: _with_keys_reversed(value[key]) for key in reversed(list(value))}
    if isinstance(value, list):
        return [_with_keys_reversed(entry) for entry in value]
    return value


@given(_contracts)
def test_key_order_never_changes_the_hash(contract: Contract) -> None:
    """FR-CON-02: keys are sorted, so the order they appear in a file is irrelevant."""
    shuffled = msgspec.convert(_with_keys_reversed(msgspec.to_builtins(contract)), Contract)

    assert approval_hash(shuffled) == approval_hash(contract)


_UNCOVERED_EDITS: dict[str, Edit] = {
    "title": lambda c: _replace(c, title=c.title + "!"),
    "status": lambda c: _replace(c, status=Status.ABANDONED),
    "allow_overlap": lambda c: _replace(c, allow_overlap=not c.allow_overlap),
}

_COVERED_EDITS: dict[str, Edit] = {
    "summary space": lambda c: _replace(c, summary=c.summary + " "),
    "extra path": lambda c: _replace(c, paths=[*c.paths, "x"]),
    "path space": lambda c: _replace(c, paths=[c.paths[0] + " ", *c.paths[1:]]),
    "item desc space": lambda c: _with_first_item(c, desc=c.items[0].desc + " "),
    "item command": lambda c: _with_first_item(c, done_when=["new-command", str(c.items[0])]),
    # The new id is longer than any generated id, so it can never repeat one.
    "item added": lambda c: _replace(c, items=[Item(id="n" * 31, desc="x"), *c.items]),
    "extra added": lambda c: _replace(
        c,
        approved_extras=[
            *c.approved_extras,
            ApprovedExtra(
                feature="function:new",
                reason="r",
                approved_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
        ],
    ),
}


@pytest.mark.parametrize("edit", _UNCOVERED_EDITS.values(), ids=_UNCOVERED_EDITS.keys())
@given(contract=_contracts)
def test_fields_outside_the_hash_do_not_change_it(contract: Contract, edit: Edit) -> None:
    """FR-CON-02: only summary, paths, items and approved_extras are covered."""
    assert approval_hash(edit(contract)) == approval_hash(contract)


@pytest.mark.parametrize("edit", _COVERED_EDITS.values(), ids=_COVERED_EDITS.keys())
@given(contract=_contracts)
def test_every_covered_field_changes_the_hash(contract: Contract, edit: Edit) -> None:
    """FR-CON-02: an edit to a covered field, even one added space, is always detected."""
    assert approval_hash(edit(contract)) != approval_hash(contract)


@given(_contracts, _text, _text)
def test_different_summaries_give_different_hashes(contract: Contract, a: str, b: str) -> None:
    """FR-CON-02: text is never tidied, so two different strings never share a hash."""
    assert (
        approval_hash(_replace(contract, summary=a)) == approval_hash(_replace(contract, summary=b))
    ) == (a == b)


@given(_contracts, st.lists(_text, min_size=2, max_size=4))
def test_list_boundaries_cannot_be_faked(contract: Contract, paths: list[str]) -> None:
    """FR-CON-02: joining two list entries into one string cannot reproduce the same hash."""
    joined = '","'.join(paths)

    assert approval_hash(_replace(contract, paths=paths)) != approval_hash(
        _replace(contract, paths=[joined])
    )
