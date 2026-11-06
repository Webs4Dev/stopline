# Randomised tests that writing a contract never changes what it means (FR-CON-04, FR-CON-05).
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import msgspec
from hypothesis import given
from hypothesis import strategies as st

from stopline.core.contract import (
    approval_hash,
    approval_matches,
    keep_feature,
    load_contract,
    new_part,
)
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
_drafts = st.builds(
    Contract,
    schema=st.just(1),
    id=st.just("auth"),
    title=_text,
    summary=_text,
    paths=st.lists(_text, min_size=1, max_size=4),
    status=st.just(Status.DRAFT),
    allow_overlap=st.booleans(),
    items=st.lists(_item, min_size=1, max_size=4, unique_by=lambda item: item.id),
    approved_extras=st.lists(_extra, max_size=3),
)


@given(_drafts)
def test_json_draft_saved_as_toml_means_the_same(draft: Contract) -> None:
    """FR-CON-05: whatever text a JSON draft holds, the saved TOML has an identical hash."""
    with tempfile.TemporaryDirectory() as folder:
        source = Path(folder) / "draft.json"
        source.write_bytes(msgspec.json.encode(draft))

        saved = load_contract(new_part("auth", Path(folder) / "parts", source))

    assert approval_hash(saved) == approval_hash(draft)
    assert saved == draft


@given(_drafts, _feature, _text)
def test_keep_always_leaves_a_correct_signature(draft: Contract, feature: str, reason: str) -> None:
    """FR-CON-04: for any contract, a keep adds exactly one extra and the signature matches."""
    known = {extra.feature for extra in draft.approved_extras}
    feature = f"function:new-{len(feature)}" if feature in known else feature
    with tempfile.TemporaryDirectory() as folder:
        source = Path(folder) / "draft.json"
        source.write_bytes(msgspec.json.encode(draft))
        path = new_part("auth", Path(folder) / "parts", source)
        now = datetime(2026, 11, 12, tzinfo=UTC)
        with path.open("a", encoding="utf-8", newline="") as file:
            file.write(
                f'\n[approval]\nhash = "{approval_hash(load_contract(path))}"\n'
                'approved_by = "a"\napproved_at = 2026-11-10T09:12:00Z\n'
            )

        keep_feature(path, feature, reason, approved_by="Owner", now=now)
        kept = load_contract(path)

    assert approval_matches(kept)
    assert kept.approved_extras[:-1] == draft.approved_extras
    assert kept.approved_extras[-1] == ApprovedExtra(feature, reason, now)
    assert kept.items == draft.items
    assert kept.paths == draft.paths
    assert kept.summary == draft.summary
