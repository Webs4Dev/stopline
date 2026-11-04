# Open questions

Things the spec does not settle, raised under spec section 25 rule 3.

| # | Raised | Question | Needed by | Proposed answer |
|---|---|---|---|---|
| 1 | 2026-11-04 (M1) | Section 9.2 gives done_when a table form with a string `cmd`. When `shell` is false, how is that string run? | M5 | Split it into arguments like a shell would (Python `shlex`), but run it without a shell. |
