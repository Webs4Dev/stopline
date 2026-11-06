# Data models for a part contract: its fields, allowed values and limits (spec section 9.2).
# Checks that need the file name or other parts live in core/contract.py.
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

import msgspec
from msgspec import Meta

# Feature kinds from spec section 10.2. Listing them means a wildcard or a typo in the
# kind portion of an anchor is rejected, while the name portion may use * and ?.
#
# Every pattern ends in (?!\n)$ because in Python a bare $ also accepts one trailing line break.
# \Z would be simpler, but these patterns are published in the JSON Schema, which cannot use it.
_FEATURE_KEY = (
    r"^(route|page|function|method|class|component|cli|config|dependency|table):.+(?!\n)$"
)

NonEmptyStr = Annotated[str, Meta(min_length=1)]
FeatureKey = Annotated[str, Meta(pattern=_FEATURE_KEY)]
PartId = Annotated[str, Meta(pattern=r"^[a-z0-9][a-z0-9-]{0,47}(?!\n)$")]
Timestamp = Annotated[datetime, Meta(tz=True)]


class Status(StrEnum):
    """The states a part can be in (spec section 8)."""

    DRAFT = "draft"
    APPROVED = "approved"
    ACTIVE = "active"
    COMPLETE = "complete"
    ABANDONED = "abandoned"


class _Model(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """Shared settings: values never change after loading, and unknown fields are errors."""


class DoneWhenCommand(_Model, frozen=True):
    """Table form of done_when. A shell is used only when `shell = true` is written."""

    cmd: NonEmptyStr
    shell: bool = False
    timeout_s: Annotated[int, Meta(ge=1, le=900)] = 120
    cwd: NonEmptyStr | None = None


class Item(_Model, frozen=True):
    """One thing the part must deliver."""

    id: NonEmptyStr
    desc: NonEmptyStr
    anchors: Annotated[list[FeatureKey], Meta(max_length=10)] = []
    done_when: Annotated[list[NonEmptyStr], Meta(min_length=1)] | DoneWhenCommand | None = None


class ApprovedExtra(_Model, frozen=True):
    """A feature outside the items that the owner chose to keep."""

    feature: FeatureKey
    reason: NonEmptyStr
    approved_at: Timestamp


class Approval(_Model, frozen=True):
    """Written by `part approve`, never by hand."""

    hash: Annotated[str, Meta(pattern=r"^sha256:[0-9a-f]{64}(?!\n)$")]
    approved_by: NonEmptyStr
    approved_at: Timestamp


class Baseline(_Model, frozen=True):
    """Written by `part start`, never by hand."""

    commit: Annotated[str, Meta(pattern=r"^[0-9a-f]{7,64}(?!\n)$")]
    started_at: Timestamp


class Contract(_Model, frozen=True):
    """A part contract, as stored in `.stopline/parts/<id>.toml`."""

    schema: Literal[1]
    id: PartId
    title: NonEmptyStr
    summary: Annotated[str, Meta(min_length=1, max_length=300)]
    paths: Annotated[list[NonEmptyStr], Meta(min_length=1, max_length=20)]
    status: Status
    items: Annotated[list[Item], Meta(min_length=1, max_length=12)]
    allow_overlap: bool = False
    approved_extras: list[ApprovedExtra] = []
    approval: Approval | None = None
    baseline: Baseline | None = None

    def __post_init__(self) -> None:
        """Reject two items with the same id (spec section 9.2)."""
        ids = [item.id for item in self.items]
        repeated = sorted({item_id for item_id in ids if ids.count(item_id) > 1})
        if repeated:
            raise ValueError(f"item ids must be unique within a part, repeated: {repeated}")
