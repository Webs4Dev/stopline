# Reads and writes contract files, reports problems by file, line and field, produces the JSON
# Schema, and calculates the approval hash. tomlkit is imported only when writing (spec 18.3).
import difflib
import hashlib
import json
import os
import re
import stat
import tempfile
import tomllib
from collections.abc import Mapping, MutableMapping
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

import msgspec

from stopline.core.models import (
    Approval,
    ApprovedExtra,
    Baseline,
    Contract,
    DoneWhenCommand,
    Item,
    PartId,
    Status,
)

if TYPE_CHECKING:
    from tomlkit import TOMLDocument

SCHEMA_URL = (
    "https://raw.githubusercontent.com/Webs4Dev/stopline/main/"
    "src/stopline/schemas/contract.v1.schema.json"
)

# Which model owns each table, used to suggest the right name for a misspelled field.
_MODELS: dict[str, type[msgspec.Struct]] = {
    "": Contract,
    "items": Item,
    "items.done_when": DoneWhenCommand,
    "approved_extras": ApprovedExtra,
    "approval": Approval,
    "baseline": Baseline,
}

_MANAGED_FIX = (
    "Stopline writes this field. Undo the manual edit with `git restore`, "
    "then use the `stopline part` commands."
)

# How to fix a bad value, by field. A field not listed here uses its parent's entry.
_FIXES = {
    "schema": "Set `schema = 1`, the only supported version.",
    "id": "Use 1-48 lower-case letters, digits or dashes, starting with a letter or digit.",
    "title": "Write a non-empty title.",
    "summary": "Write a summary of 1-300 characters.",
    "paths": 'List 1-20 non-empty globs relative to the repo root, such as "src/auth/**".',
    "status": _MANAGED_FIX,
    "allow_overlap": "Use true or false.",
    "items": "List 1-12 items, each with its own id and a desc.",
    "items.id": "Write a non-empty id that no other item in this part uses.",
    "items.desc": "Write a non-empty description.",
    "items.anchors": (
        'Use at most 10 keys written as kind:name, such as "route:POST /login". Kinds: route, '
        "page, function, method, class, component, cli, config, dependency, table."
    ),
    "items.done_when": 'Use a list like ["pytest", "-q"] or a table like {cmd = "pytest -q"}.',
    "items.done_when.timeout_s": "Use a whole number of seconds from 1 to 900.",
    "approved_extras": (
        "Each entry needs a feature written as kind:name, a reason and a time with a timezone. "
        "Add entries with `stopline part keep FEATURE --reason TEXT`."
    ),
    "approval": _MANAGED_FIX,
    "baseline": _MANAGED_FIX,
}

_TEMPLATE = """\
#:schema {schema_url}
schema  = 1
id      = "{part_id}"
title   = "{part_id}"
summary = "Say in one sentence what this part delivers."
paths   = ["src/{part_id}/**"]
status  = "draft"

# List what this part must deliver: 1-12 items, 3-7 recommended. An item needs anchors or a
# done_when, or it can never be verified and the part cannot complete.
[[items]]
id   = "first-item"
desc = "Say what this item delivers."
# anchors   = ["route:POST /example"]
# done_when = ["pytest", "tests/test_example.py", "-q"]
"""

_TABLE_HEADER = re.compile(r"\s*\[\[?\s*([\w.-]+)\s*\]\]?\s*(#.*)?$")
_PATH_PART = re.compile(r"\.(\w+)|\[(\d+)\]")
_FIELD_NAMED = re.compile(r"Object (missing required|contains unknown) field `(\w+)`")
_TOML_LINE = re.compile(r"at line (\d+)")


class ContractError(Exception):
    """A contract that cannot be used, with where the problem is and how to fix it (FR-CON-01)."""

    def __init__(
        self,
        source: Path,
        problem: str,
        fix: str,
        line: int | None = None,
        field: str | None = None,
    ) -> None:
        self.source = source
        self.problem = problem
        self.fix = fix
        self.line = line
        self.field = field
        where = str(source) if line is None else f"{source}:{line}"
        subject = problem if field is None else f"`{field}`: {problem}"
        super().__init__(f"{where}: {subject}\n  Fix: {fix}")


def load_contract(path: Path) -> Contract:
    """Read and validate a contract file, raising ContractError if anything is wrong."""
    return load_with_text(path)[0]


def load_with_text(path: Path) -> tuple[Contract, str]:
    """Return a validated contract together with the exact text it was read from."""
    text = _read_text(path, missing_fix=f"Create it with `stopline part new {path.stem}`.")
    contract = _parse(text, path)
    if contract.id != path.stem:
        raise ContractError(
            path,
            f"The id is {contract.id!r} but the file is named {path.name!r}; they must match.",
            f"Change the id to {path.stem!r}, or rename the file to {contract.id}.toml.",
            line=_find_line(text, [("id", 0)]),
            field="id",
        )
    return contract, text


def new_part(part_id: str, parts_dir: Path, source: Path | None = None) -> Path:
    """Create a draft contract and return its path (spec section 8, FR-CON-05).

    With no source it is written from a template. With a source, an agent's TOML or JSON draft is
    saved as a draft: a missing id or status is filled in, and a draft that claims to be approved
    or started is refused, so only the owner's `part approve` can ever make it approved.
    """
    path = parts_dir / f"{part_id}.toml"
    try:
        msgspec.convert(part_id, PartId)
    except msgspec.ValidationError:
        fix = "Use 1-48 lower-case letters, digits or dashes, starting with a letter or digit."
        raise ContractError(path, f"{part_id!r} is not a valid part id.", fix) from None
    if path.exists():
        fix = "Choose another id, or edit the existing contract."
        raise ContractError(path, f"Part `{part_id}` already exists.", fix)

    if source is None:
        text = _TEMPLATE.format(schema_url=SCHEMA_URL, part_id=part_id)
    else:
        text = _draft_text(part_id, source)
    _parse(text, path)  # nothing unreadable is ever saved
    _write_atomic(path, text)
    return path


def keep_feature(path: Path, feature: str, reason: str, approved_by: str, now: datetime) -> None:
    """Add an approved extra and re-sign the contract in one step (FR-CON-04).

    A contract edited since its last approval is refused. Signing it here would approve those
    edits without the owner ever seeing them.
    """
    import tomlkit
    from tomlkit.items import Array

    # The signature is checked on the same text that gets edited, so a change made to the file
    # after the check can never be signed by accident.
    contract, original = load_with_text(path)
    if contract.status is Status.ABANDONED:
        fix = "Abandoned parts are kept for history. Add the feature to another part."
        raise ContractError(path, f"Part `{contract.id}` is abandoned.", fix, field="status")
    if not approval_matches(contract):
        problem = f"Part `{contract.id}` is not approved, or changed since it was approved."
        fix = f"Review the contract, then run `stopline part approve {contract.id}`."
        raise ContractError(path, problem, fix, field="approval")
    if any(extra.feature == feature for extra in contract.approved_extras):
        fix = "There is nothing to do."
        raise ContractError(path, f"`{feature}` is already an approved extra.", fix)
    values = {"feature": feature, "reason": reason, "approved_at": now}
    try:
        msgspec.convert(values, ApprovedExtra)
    except msgspec.ValidationError:
        problem = f"Cannot keep {feature!r} with reason {reason!r}."
        fix = 'Write the feature as kind:name, such as "route:POST /login", and give a reason.'
        raise ContractError(path, problem, fix) from None

    document = tomlkit.parse(original)
    extras = document.get("approved_extras")
    if isinstance(extras, Array):  # written by hand as `approved_extras = [...]`
        extras.append(values)
    else:
        entry = tomlkit.table()
        entry.update(values)
        if extras is None:
            document.add(tomlkit.nl())  # a blank line before the new table
            extras = tomlkit.aot()
            extras.append(entry)
            document["approved_extras"] = extras
        else:
            entry.add(tomlkit.nl())  # a blank line before any table that follows
            extras.append(entry)

    sign_document(document, path, approved_by, now)
    save_document(document, path)


def add_table(document: "TOMLDocument", key: str, values: Mapping[str, object]) -> None:
    """Add a table at the end of a contract, with a blank line before it."""
    import tomlkit

    table = tomlkit.table()
    table.update(values)
    document.add(tomlkit.nl())
    document[key] = table


def sign_document(document: "TOMLDocument", path: Path, approved_by: str, now: datetime) -> None:
    """Record the fingerprint of the document as it stands, and who approved it and when."""
    import tomlkit

    values = {
        "hash": approval_hash(_parse(tomlkit.dumps(document), path)),
        "approved_by": approved_by,
        "approved_at": now,
    }
    if "approval" in document:
        cast("MutableMapping[str, object]", document["approval"]).update(values)
    else:
        add_table(document, "approval", values)


def save_document(document: "TOMLDocument", path: Path) -> None:
    """Validate a changed contract, then save it atomically."""
    import tomlkit

    text = tomlkit.dumps(document)
    _parse(text, path)  # nothing unreadable is ever saved
    _write_atomic(path, text)


def contract_warnings(contract: Contract) -> list[str]:
    """Return problems that do not stop a contract from loading (FR-CON-06)."""
    return [
        f"Item `{item.id}` has neither anchors nor done_when, so it can never be verified and "
        f"part `{contract.id}` cannot complete. Add an anchor or a done_when."
        for item in contract.items
        if not item.anchors and item.done_when is None
    ]


def approval_hash(contract: Contract) -> str:
    """Return the SHA-256 fingerprint of what the owner approves (FR-CON-02).

    The JSON has sorted keys and no spacing between elements. Text inside values is left exactly
    as written, because tidying it could change what a done_when command does without breaking
    the approval. Changing these bytes, or the fields of the models they hold, invalidates every
    existing approval.
    """
    canonical = msgspec.json.encode(
        {
            "allow_overlap": contract.allow_overlap,
            "approved_extras": contract.approved_extras,
            "items": contract.items,
            "paths": contract.paths,
            "summary": contract.summary,
        },
        order="sorted",
    )
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def approval_matches(contract: Contract) -> bool:
    """Return whether the contract is signed and unchanged since it was signed (FR-CON-03)."""
    return contract.approval is not None and contract.approval.hash == approval_hash(contract)


def contract_json_schema() -> dict[str, object]:
    """Return the published JSON Schema for contracts, generated from the models."""
    schema: dict[str, object] = msgspec.json.schema(Contract)
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": SCHEMA_URL, **schema}


def contract_json_schema_text() -> str:
    """Return the JSON Schema in the exact form stored in src/stopline/schemas/."""
    return json.dumps(contract_json_schema(), indent=2, sort_keys=True) + "\n"


def _read_text(path: Path, missing_fix: str) -> str:
    """Read a UTF-8 file, keeping its line endings, or raise ContractError."""
    try:
        return path.read_bytes().decode("utf-8")
    except FileNotFoundError:
        raise ContractError(path, "The file does not exist.", missing_fix) from None
    except OSError as err:
        fix = "Check that the path is a file you have permission to read."
        raise ContractError(path, f"The file cannot be read: {err.strerror}.", fix) from None
    except UnicodeDecodeError:
        raise ContractError(path, "The file is not valid UTF-8.", "Save it as UTF-8.") from None


def _write_atomic(path: Path, text: str) -> None:
    """Save a file so that a crash leaves either the old version or the new one (FR-CON-07)."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
        descriptor, temp_name = tempfile.mkstemp(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
        )
        temp = Path(temp_name)
        try:
            with os.fdopen(descriptor, "wb") as temp_file:
                temp_file.write(text.encode("utf-8"))
                temp_file.flush()
                os.fsync(temp_file.fileno())
            temp.chmod(mode)  # temp files are private by default; contracts are not
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)
    except OSError as err:
        fix = "Check that you have permission to write to this folder and that the disk has space."
        raise ContractError(path, f"The file cannot be saved: {err.strerror}.", fix) from None


def _draft_text(part_id: str, source: Path) -> str:
    """Check an agent's draft and return it as the TOML text to save."""
    text = _read_text(source, missing_fix="Check the path of the draft file.")
    is_json = source.suffix.lower() == ".json"
    data = _read_json(text, source) if is_json else _read_toml(text, source)

    for field in ("approval", "baseline"):
        if data.get(field) is not None:  # JSON may spell "absent" as null
            problem = "A draft cannot carry this; Stopline writes it."
            line = _find_line(text, [(field, 0)])
            raise ContractError(source, problem, f"Remove `{field}`.", line=line, field=field)
    for field, allowed in (("id", part_id), ("status", Status.DRAFT.value)):
        if data.get(field, allowed) != allowed:
            problem = f"In this draft it must be {allowed!r}, but it is {data[field]!r}."
            fix = f'Change it to "{allowed}", or remove the line.'
            line = _find_line(text, [(field, 0)])
            raise ContractError(source, problem, fix, line=line, field=field)

    contract = _validate({**data, "id": part_id, "status": Status.DRAFT.value}, text, source)
    if is_json:
        return _contract_as_toml(contract)
    return _with_filled_fields(text, data, part_id)


def _read_json(text: str, source: Path) -> dict[str, object]:
    """Parse JSON text. msgspec, unlike json.loads, refuses text that cannot be saved as UTF-8."""
    try:
        data = msgspec.json.decode(text.encode("utf-8"))
    except msgspec.DecodeError as err:
        raise ContractError(source, f"Invalid JSON: {err}.", "Correct the JSON syntax.") from None
    if not isinstance(data, dict):
        raise ContractError(source, "The draft must be a JSON object.", "Wrap it in { }.")
    return data


def _with_filled_fields(text: str, data: dict[str, object], part_id: str) -> str:
    """Add what a TOML draft left out at the top, leaving everything it wrote untouched."""
    first_line, _, rest = text.partition("\n")
    if first_line.startswith("#:schema"):
        lines = [first_line]
    else:
        lines, rest = [f"#:schema {SCHEMA_URL}"], text
    if "id" not in data:
        lines.append(f'id = "{part_id}"')
    if "status" not in data:
        lines.append(f'status = "{Status.DRAFT.value}"')
    return "\n".join([*lines, rest])


def _contract_as_toml(contract: Contract) -> str:
    """Write a validated contract as TOML, for drafts that arrived as JSON."""
    import tomlkit

    built = _without_nulls(msgspec.to_builtins(contract, builtin_types=(datetime,)))
    data = cast("dict[str, object]", built)
    # Tables go last: in TOML, anything written after a table would end up inside it.
    tables = {key: data.pop(key) for key in ("items", "approved_extras")}

    document = tomlkit.document()
    document.update(data)
    document.update({key: value for key, value in tables.items() if value})
    return f"#:schema {SCHEMA_URL}\n{tomlkit.dumps(document)}"


def _without_nulls(value: object) -> object:
    """Drop empty optional fields, because TOML has no way to write null."""
    if isinstance(value, dict):
        return {key: _without_nulls(entry) for key, entry in value.items() if entry is not None}
    if isinstance(value, list):
        return [_without_nulls(entry) for entry in value]
    return value


def _parse(text: str, source: Path) -> Contract:
    """Turn TOML text into a validated Contract."""
    return _validate(_read_toml(text, source), text, source)


def _read_toml(text: str, source: Path) -> dict[str, object]:
    """Turn TOML text into plain data, or raise ContractError with the line."""
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as err:
        found = _TOML_LINE.search(str(err))
        # The parser reports "at end of document" instead of a line for some errors.
        line = int(found.group(1)) if found else text.count("\n") + 1
        fix = "Correct the TOML syntax on that line or the one before it."
        raise ContractError(source, f"Invalid TOML: {err}.", fix, line=line) from None


def _validate(data: dict[str, object], text: str, source: Path) -> Contract:
    """Turn plain data into a Contract, or raise ContractError pointing into the text."""
    try:
        return msgspec.convert(data, Contract)
    except msgspec.ValidationError as err:
        raise _describe(str(err), text, source) from None


def _describe(raw: str, text: str, source: Path) -> ContractError:
    """Turn a msgspec message such as "... - at `$.items[0].id`" into a ContractError."""
    message, separator, location = raw.rpartition(" - at `")
    if not separator:
        message, location = raw, ""
    location = location.rstrip("`")
    path = _parse_path(location)
    field = location.removeprefix("$").removeprefix(".")

    if message.startswith("item ids"):  # raised by Contract.__post_init__, which has no location
        path, field = [("items", 0)], "items"
    named = _FIELD_NAMED.match(message)
    if named is None:
        fix = _fix_for(path)
    else:
        key = named.group(2)
        if named.group(1) == "contains unknown":
            fix = _unknown_field_fix(key, ".".join(name for name, _ in path))
            path = [*path, (key, 0)]  # the key exists in the file, so point at its line
        else:
            fix = f"Add `{key}`."
        field = f"{field}.{key}" if field else key

    problem = re.sub(r"matching regex '.*'", "in the allowed format", message)
    return ContractError(source, f"{problem}.", fix, line=_find_line(text, path), field=field)


def _parse_path(location: str) -> list[tuple[str, int]]:
    """Split "$.items[1].anchors[0]" into [("items", 1), ("anchors", 0)]."""
    path: list[tuple[str, int]] = []
    for name, index in _PATH_PART.findall(location):
        if name:
            path.append((name, 0))
        else:
            path[-1] = (path[-1][0], int(index))
    return path


def _fix_for(path: list[tuple[str, int]]) -> str:
    """Return the fix for a field, or for its nearest parent that has one."""
    names = [name for name, _ in path]
    while names:
        fix = _FIXES.get(".".join(names))
        if fix is not None:
            return fix
        names.pop()
    return "Compare the file with the output of `stopline schema contract`."


def _unknown_field_fix(key: str, table: str) -> str:
    """Suggest the closest real field name for a misspelled one."""
    allowed = _MODELS[table].__struct_fields__
    close = difflib.get_close_matches(key, allowed, n=1)
    if close:
        return f"Did you mean `{close[0]}`? Otherwise remove `{key}`."
    return f"Remove `{key}`. Allowed fields here: {', '.join(allowed)}."


def _find_line(text: str, path: list[tuple[str, int]]) -> int:
    """Find the 1-based line of a field: its key, else the table that should hold it, else 1.

    Validation errors carry no line numbers, so this reads the text. It is a best-effort
    pointer for the person fixing the file and never affects whether a contract is valid.
    """
    lines = text.split("\n")
    headers = [
        (number, found.group(1))
        for number, line in enumerate(lines)
        if (found := _TABLE_HEADER.match(line))
    ]
    best = 1
    prefix = ""
    start, stop = 0, len(lines)
    for name, index in path:
        body_end = next((n for n, _ in headers if start <= n < stop), stop)
        key = re.compile(rf"""\s*["']?{re.escape(name)}["']?\s*=""")
        for number in range(start, body_end):
            if key.match(lines[number]):
                return number + 1
        dotted = f"{prefix}{name}"
        matches = [n for n, header in headers if header == dotted and start <= n < stop]
        if index >= len(matches):
            return best
        best = matches[index] + 1
        if index + 1 < len(matches):
            stop = matches[index + 1]
        start = matches[index] + 1
        prefix = f"{dotted}."
    return best
