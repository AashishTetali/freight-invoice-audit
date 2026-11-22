# AI Usage Disclosure

I used **Claude Code** (Anthropic's coding agent, running Claude models) as my main development tool for
this project. I wrote the requirements and direction; Claude Code wrote nearly all of the code, tests and
documentation under my instruction. I [read / ran / questioned] the results as described below.

The full prompt history is in [`PROMPT_HISTORY.md`](PROMPT_HISTORY.md) .

## Where AI accelerated my work

| Area | What Claude Code did |
|---|---|
| Reading the problem | Read my planning notes and the submission guidelines and turned the brief into a build plan |
| Code | Wrote snippets or parts of the code in the generator, audit engine, pricing maths, validation, evaluation, exports, dashboard and command line based on given plan |
| Tests | Wrote the unit tests (81) and integration tests (21), including negative scenarios that break the data on purpose |
| Documentation | Drafted the README, the data dictionary generator and architecture explanations |
| Debugging | Found and fixed bugs it had introduced (see "Problems found along the way") |

## Where I relied on it heavily

- **some part of the Python code.** I directed the design, file structure, file functionalities and Claude Code implemented some parts of it.
- **The pricing and audit rules** (DIM weight, weight bands, fuel surcharge, the $5 dispute threshold, the refund
  reconciliation). 
- **The test suite and the README text.**


## Problems found along the way (and fixed)

These were found by running the project, not assumed away:

1. A rounding error in size-based (DIM) weight: 663 lb should have been 664 lb.
2. Late deliveries were generated far too often (29%), corrected to about 12.5%.
3. The reproducibility check compared incomplete runs, fixed so both runs build everything.
4. Windows line endings changed file checksums, fixed with `.gitattributes`.
5. A test word-list wrongly matched `issued_date`.
6. The validator could crash on broken data instead of reporting it; it now reports a failed check.
7. The dashboard crashed on mixed-type columns; fixed.

## How I know the solution works

- 102 automated tests (81 unit, 21 integration).
- 168 data-quality checks, all passing, on both the starter and full datasets.
- Detection graded against a separate answer key the audit engine never reads (a test verifies this).
- Building twice from the same seed gives byte-identical files (SHA-256 of 48 files).

## Limits I want to be upfront about

- The score is 100% because the data generator and the audit engine share the same written rules. That shows the
  code is consistent and correct, not that real freight audits would be perfect.
- All data is synthetic.

## Tools

- Claude Code (Anthropic) with Claude models, for code, tests, documentation and debugging.

