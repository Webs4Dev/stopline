# Open questions

Things the spec does not settle, raised under spec section 25 rule 3.

| # | Raised | Question | Needed by | Proposed answer |
|---|---|---|---|---|
| 1 | 2026-11-04 (M1) | Section 9.2 gives done_when a table form with a string `cmd`. When `shell` is false, how is that string run? | M5 | Split it into arguments like a shell would (Python `shlex`), but run it without a shell. |
| 2 | 2026-11-06 (M1) | FR-CON-02 hashes only summary, paths, items and approved_extras, and FR-CON-08 records only hash, status and baseline. So nothing detects a hand edit to `allow_overlap`, which section 9.2 uses to decide whether two parts may share paths. Should it be protected? | M1 step 6 (the overlap check) | Add `allow_overlap` to the approval hash. `title` can stay out because it changes no behaviour. |
