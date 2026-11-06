# Contract reference

A part contract lists what one part of your project must deliver and how to verify it. Each one is a TOML file at `.stopline/parts/<id>.toml`, saved as UTF-8 and committed to git.

## Example

```toml
#:schema https://raw.githubusercontent.com/Webs4Dev/stopline/main/src/stopline/schemas/contract.v1.schema.json
schema  = 1
id      = "auth"
title   = "Authentication"
summary = "Email + password login with JWT access and refresh tokens"
paths   = ["src/auth/**", "tests/auth/**"]
status  = "draft"

[[items]]
id        = "login"
desc      = "POST /auth/login returns access and refresh tokens"
anchors   = ["route:POST /auth/login"]
done_when = ["uv", "run", "pytest", "tests/auth/test_login.py", "-q"]

[[items]]
id        = "refresh"
desc      = "POST /auth/refresh issues a new access token"
anchors   = ["route:POST /auth/refresh", "function:auth.tokens.issue_*"]
done_when = { cmd = "pytest tests/auth/test_refresh.py -q && ruff check src/auth", shell = true, timeout_s = 120 }
```

## Fields

| Field | Required | Rules |
|---|---|---|
| `schema` | Yes | Must be `1`. |
| `id` | Yes | 1-48 lower-case letters, digits or dashes, starting with a letter or digit. Must match the file name. |
| `title` | Yes | Not empty. |
| `summary` | Yes | 1-300 characters. |
| `paths` | Yes | 1-20 gitignore-style globs, relative to the repository root. |
| `status` | Yes | `draft`, `approved`, `active`, `complete` or `abandoned`. Stopline manages it. |
| `allow_overlap` | No | `true` lets this part share paths with another part that also sets it. Default `false`. |
| `items` | Yes | 1-12 items (3-7 is a good size). See below. |
| `approved_extras` | No | Features you chose to keep. Add them with `stopline part keep`. |
| `approval` | No | Written by `stopline part approve` and `part keep`. Do not edit it. |
| `baseline` | No | Written by `stopline part start`. Do not edit it. |

### Items

| Field | Required | Rules |
|---|---|---|
| `id` | Yes | Not empty, and unique within the part. |
| `desc` | Yes | Not empty. |
| `anchors` | No | Up to 10 features written as `kind:name`. The name may use the wildcards `*` and `?`. Kinds: `route`, `page`, `function`, `method`, `class`, `component`, `cli`, `config`, `dependency`, `table`. |
| `done_when` | No | The command that proves the item works: a list such as `["pytest", "-q"]`, which runs without a shell, or a table. |

The table form of `done_when` has these fields:

| Field | Required | Rules |
|---|---|---|
| `cmd` | Yes | The command, as one string. |
| `shell` | No | Runs the command in a shell only when set to `true`. Default `false`. |
| `timeout_s` | No | 1-900 seconds. Default 120. |
| `cwd` | No | The folder to run it in. |

An item with neither `anchors` nor `done_when` can never be verified, so its part cannot complete. Stopline warns you about this.

### Approved extras

| Field | Rules |
|---|---|
| `feature` | Written as `kind:name`. |
| `reason` | Not empty. |
| `approved_at` | A time with a timezone. |

## Errors

A contract that breaks these rules is refused, with the file, line, field and a suggested fix:

```text
.stopline/parts/auth.toml:12: `items[0].anchors[0]`: Expected `str` in the allowed format.
  Fix: Use at most 10 keys written as kind:name, such as "route:POST /login". Kinds: route, ...
```

Line numbers are a best-effort pointer. In a JSON draft every problem is reported at line 1, but the field name is still exact.

## What approval protects

`stopline part approve` stores a SHA-256 fingerprint in `approval.hash`. If the contract changes afterwards, the fingerprint stops matching and the contract counts as unapproved until you approve it again.

| Change | Approval still valid? |
|---|---|
| `summary`, `paths`, `items`, `approved_extras` or `allow_overlap` changed, even by one space | No |
| The order of paths, items, or a command's arguments changed | No |
| Comments, spacing outside values, key order or line endings changed | Yes |
| A default written out, such as `shell = false` | Yes |
| `title` changed | Yes |
| `status` or `baseline` changed | Yes. Local trust records will protect these from milestone M2. |

Text inside values is hashed exactly as written, because even a double space inside a command can change what it does.

## Drafts from an agent

An agent can save a draft with `stopline part new ID --from FILE`, as TOML or JSON. The draft may leave out `id` and `status`, and Stopline fills them in. TOML comments are kept. A draft is refused if it has a different `id`, any `status` other than `draft`, or its own `approval` or `baseline`.

## JSON Schema

`stopline schema contract` prints the schema, which is also published at `src/stopline/schemas/contract.v1.schema.json`. The `#:schema` line at the top of a contract lets editors that support it, such as VS Code with Even Better TOML, validate the file as you type.
