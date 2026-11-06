# Reads contract files, reports problems by file, line and field, produces the JSON Schema,
# and calculates the approval hash.
import difflib
import hashlib
import json
import re
import tomllib
from pathlib import Path

import msgspec

from stopline.core.models import (
    Approval,
    ApprovedExtra,
    Baseline,
    Contract,
    DoneWhenCommand,
    Item,
)

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
    try:
        text = path.read_bytes().decode("utf-8")
    except FileNotFoundError:
        fix = f"Create it with `stopline part new {path.stem}`."
        raise ContractError(path, "The contract file does not exist.", fix) from None
    except OSError as err:
        fix = "Check that the path is a file you have permission to read."
        raise ContractError(path, f"The file cannot be read: {err.strerror}.", fix) from None
    except UnicodeDecodeError:
        raise ContractError(path, "The file is not valid UTF-8.", "Save it as UTF-8.") from None

    contract = _parse(text, path)
    if contract.id != path.stem:
        raise ContractError(
            path,
            f"The id is {contract.id!r} but the file is named {path.name!r}; they must match.",
            f"Change the id to {path.stem!r}, or rename the file to {contract.id}.toml.",
            line=_find_line(text, [("id", 0)]),
            field="id",
        )
    return contract


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


def _parse(text: str, source: Path) -> Contract:
    """Turn TOML text into a validated Contract."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as err:
        found = _TOML_LINE.search(str(err))
        # The parser reports "at end of document" instead of a line for some errors.
        line = int(found.group(1)) if found else text.count("\n") + 1
        fix = "Correct the TOML syntax on that line or the one before it."
        raise ContractError(source, f"Invalid TOML: {err}.", fix, line=line) from None
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
