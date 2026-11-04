# ADR 004: git CLI over libgit2

- Status: accepted
- Date: 2026-11-03 (spec v2.0, section 18.3)

## Context and problem statement

Stopline needs the baseline commit, changed and untracked files, file contents at a commit, and an exact working-tree hash.

## Considered options

- git command-line tool via subprocess
- pygit2 (libgit2, native dependency)
- dulwich

## Decision outcome

The git CLI. It is present wherever agents run, needs no native build, and behaves as users expect.

## Consequences

- Speed is adequate with caching keyed by git blob hash (FR-EX-08).
- Subprocess calls sit behind a small interface so core logic is testable without a repository.
