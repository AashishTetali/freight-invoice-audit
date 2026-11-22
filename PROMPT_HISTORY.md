# Prompt History

```
You are helping me build a project. I have already decided the design; your job
is to implement it, not redesign it.

PROJECT: Freight Invoice Audit. Validate carrier freight invoices against rate
cards, shipments, accessorials and delivery records.

DESIGN (my decisions, do not change without asking):
- Two halves: a world builder (generator) that plants labelled errors, and a
  detective (audit engine) that never reads the answer key. A grader compares them.
- Python standard library only; money as Decimal rounded half-up; fixed seed 20261022.
- One schema file drives the SQL, the validation checks and the data dictionary.
- Status changes are append-only history rows.

STRUCTURE (create exactly these, populate only the file I name in each message):
  freight_audit/ config.py schema.py reference.py pricing.py generator.py
                 audit.py settlement.py history.py validate.py evaluate.py
                 export.py pipeline.py __main__.py
  tests/unit/  tests/integration/   dashboard/app.py

RULES FOR YOU:
- Implement only the file I name. Do not edit other files.
- If my spec is ambiguous or contradicts itself, ask before guessing.
- After each file: run its tests and show me the result and the diff.
- Tell me anything you assumed.

Reply "ready" and wait for the first file.
```

## Per-file prompt

```
Now implement freight_audit/<FILE>.py.

PURPOSE: <one sentence>
INPUTS: <what it receives, with types>
OUTPUTS: <what it returns or writes>
RULES:
- <exact rule 1, with formula or example>
- <exact rule 2>
DO NOT: <e.g. read ground_truth, use float for money>
TESTS: add tests/unit/test_<FILE>.py covering <case A>, <case B>, <failure case>.
Run them and show me the output.
```

## Example filled in (pricing.py)

```
Now implement freight_audit/pricing.py.

PURPOSE: compute what a shipment SHOULD cost from the contract.
INPUTS: shipment, its latest weight measurement, the carrier's rate card for the ship date, delivery record.
OUTPUTS: expected base, fuel, accessorial amounts and billable weight, all Decimal.
RULES:
- dim_weight = ceil(L*W*H*pieces / dim_factor); factor 139 or 166 per contract
- billable_weight = max(ceil(actual), dim_weight)
- base = max(billable_weight * band_rate, minimum_charge)
- fuel = base * fuel_pct; accessorials only if on the delivery record AND in the contract
- worked check: 48x40x48 in, 300 lb, factor 139 -> 664 lb
DO NOT: use float for money; read ground_truth.
TESTS: DIM-weight case, minimum-charge case, expired-rate-card case.
```
