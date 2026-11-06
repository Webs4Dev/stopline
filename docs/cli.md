# CLI reference

Stopline is in early development, so this page lists only what works today. Run any command with `--help` for its options. The final page will be generated from the code.

## Commands

| Command | What it does | Who can run it |
|---|---|---|
| `stopline --version` | Print the version. | Anyone |
| `stopline schema contract` | Print the contract JSON Schema. | Anyone |
| `stopline part new ID [--from FILE]` | Create a draft contract, from a template or an agent's TOML or JSON draft. | Anyone |
| `stopline part show ID` | Print a contract, every command it may run, and whether its approval is valid. Item status arrives in milestone M5. | Anyone |
| `stopline part list` | List all parts with their status and counts. | Anyone |
| `stopline part start ID [--allow-dirty]` | Make an approved part active and record the current commit as its baseline. Refuses uncommitted changes in the part's paths unless `--allow-dirty` is given. | Anyone |
| `stopline part keep FEATURE [--part ID] [--reason TEXT]` | Add an unplanned feature to the contract and re-sign it. Without `--part` it uses the active part. Without `--reason`, a terminal asks for one and anything else is an error. | Anyone (see the limitation below) |
| `stopline part approve ID` | Show a draft or edited contract, ask for confirmation, and sign it. | Owner |
| `stopline part pause` | Move the active part back to approved, keeping its baseline. | Owner |
| `stopline part reopen ID` | Make a complete part active again. | Owner |
| `stopline part abandon ID` | Retire a part that is not complete. It stays on disk for history. | Owner |

Part commands work from any folder inside a git repository.

## Stages of a part

```text
draft --approve--> approved --start--> active --all items satisfied--> complete
                   approved <--pause-- active <--------reopen--------- complete
```

Any part except a complete one can be abandoned. Only one part can be active at a time. Two parts cannot share paths unless both set `allow_overlap = true`; this check catches identical and nested globs, such as `src/**` and `src/auth/**`, but not every possible overlap.

If you edit an approved, active or complete contract, run `stopline part approve` again. It signs the new version and leaves the status and baseline as they were.

## Owner-only commands

These refuse to run without an interactive terminal, because an agent's shell does not have one. Scripts you control can set `STOPLINE_ALLOW_NONINTERACTIVE=1`.

## Limitations

Stopline guards against accidental overbuilding. It is not a sandbox, and an agent with full shell access could get around any local tool, for example by setting that variable itself.

An agent may run `part keep`, because you are meant to confirm it in the agent's own permission prompt. That prompt comes from Stopline's hook, which arrives in milestone M6. **Until then, and whenever the hooks are not installed or have failed, or the agent runs in a mode that skips permission prompts, such as Claude Code's `bypassPermissions`, nothing stops an agent running `part keep`.** It cannot sign other changes, because it refuses a contract edited since its last approval, and what it adds shows up in `git diff`.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success. |
| 3 | Contract or configuration problem. |
| 4 | Usage error, including an owner-only command run without a terminal. |
| 5 | Environment problem: git is missing, this is not a repository, or git has no `user.name`. |
| 70 | Internal error, which is a bug. The message says where to report it. |

Codes 1 and 2 are reserved for findings from `stopline check`, which arrives in a later milestone.
