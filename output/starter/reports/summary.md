# Freight invoice audit - results (starter, seed 20261022)

## What happened

* 750 carrier invoices were audited against contracts and delivery records (1,000 shipments in total).
* 646 were correct (86.1%), 89 had line-level problems and 15 were rejected outright (duplicates / unknown shipments).
* Total billed $321,010.27. Rejected bills worth $6,588.46; line overbilling found $7,575.18; disputed $7,550.16; credits applied $7,137.36; over-limit credits held $185.08.
* Money saved (rejected bills + credits): **$13,725.82** (4.3% of billed).

## How well the audit engine found the planted problems

Overall precision **100.0%**, recall **100.0%**; false alarms on clean invoices: 0 of 646. Decisions matching the rules: 100.0%; $ variance matching the answer key: 100.0%.

| Issue type | Planted | Detected | Precision | Recall |
|---|---|---|---|---|
| UNKNOWN_SHIPMENT | 4 | 4 | 100.0% | 100.0% |
| DUPLICATE_INVOICE | 11 | 11 | 100.0% | 100.0% |
| RATE_CARD_EXPIRED | 7 | 7 | 100.0% | 100.0% |
| WEIGHT_TIER_MISAPPLIED | 16 | 16 | 100.0% | 100.0% |
| DIM_WEIGHT_DISCREPANCY | 18 | 18 | 100.0% | 100.0% |
| FUEL_SURCHARGE_MISCALCULATED | 9 | 9 | 100.0% | 100.0% |
| SURCHARGE_DUPLICATED | 19 | 19 | 100.0% | 100.0% |
| INVALID_ACCESSORIAL | 20 | 20 | 100.0% | 100.0% |
| CREDIT_EXCEEDS_EXCEPTION | 3 | 3 | 100.0% | 100.0% |

## Exceptions raised

| Type | Count | Total variance |
|---|---|---|
| CREDIT_EXCEEDS_EXCEPTION | 3 | $39.17 |
| DIM_WEIGHT_DISCREPANCY | 18 | $2,309.64 |
| DUPLICATE_INVOICE | 11 | $4,027.27 |
| FUEL_ON_INCORRECT_BASE | 41 | $846.89 |
| FUEL_SURCHARGE_MISCALCULATED | 9 | $79.91 |
| INVALID_ACCESSORIAL | 20 | $1,501.00 |
| RATE_CARD_EXPIRED | 7 | $69.16 |
| SURCHARGE_DUPLICATED | 19 | $1,784.35 |
| UNKNOWN_SHIPMENT | 4 | $2,561.19 |
| WEIGHT_TIER_MISAPPLIED | 16 | $984.23 |

## Example exceptions (one per type)

* `FEX-2026-000001` **INVALID_ACCESSORIAL** on `FLINE-FINV-2026-000007-05`: DETENTION billed but not recorded on delivery DLV-2026-000012 (variance $85.00)
* `FEX-2026-000002` **FUEL_SURCHARGE_MISCALCULATED** on `FLINE-FINV-2026-000019-02`: Fuel billed at 33.5% = $81.15; contract is 28.5% of $242.25 = $69.04 (variance $12.11)
* `FEX-2026-000006` **WEIGHT_TIER_MISAPPLIED** on `FLINE-FINV-2026-000042-01`: 3961 lb belongs in tier 2000-4999 lb at 0.1059/lb, but the 1000-1999 lb rate 0.1271/lb was used (variance $83.97)
* `FEX-2026-000007` **FUEL_ON_INCORRECT_BASE** on `FLINE-FINV-2026-000042-02`: Fuel 28.5% applied to the incorrect base $503.44; correct is 28.5% of $419.47 = $119.55 (knock-on of FEX-2026-000006) (variance $23.93)
* `FEX-2026-000008` **SURCHARGE_DUPLICATED** on `FLINE-FINV-2026-000045-03`: Fuel surcharge billed again (duplicate of line 2) (variance $188.60)
* `FEX-2026-000010` **DIM_WEIGHT_DISCREPANCY** on `FLINE-FINV-2026-000053-01`: Billed 2407 lb; measurements give actual 1660 lb, DIM 856 lb (factor 166), so billable is 1660 lb (variance $27.17)
* `FEX-2026-000028` **DUPLICATE_INVOICE** on `FINV-2026-000169`: Shipment SHP-2026-000157 was already billed on FINV-2026-000113 (identical amount $242.71) (variance $242.71)
* `FEX-2026-000080` **UNKNOWN_SHIPMENT** on `FINV-2026-000454`: Invoice quotes shipment SHP-2026-961912, which does not exist (variance $599.87)
* `FEX-2026-000089` **RATE_CARD_EXPIRED** on `FLINE-FINV-2026-000523-01`: Billed with RATE-CAR-000002-2026-01 valid 2026-01-01 to 2026-06-30; ship date 2026-07-11 requires RATE-CAR-000002-2026-02 (variance $8.49)
* `FEX-2026-000146` **CREDIT_EXCEEDS_EXCEPTION** on `FLINE-FINV-2026-000327-04`: Credit note CN-2026-000049 for $32.58 exceeds the $28.83 approved on FEX-2026-000056 (variance $3.75)

## Carriers with the highest exception rate

| Carrier | Invoices | With exceptions | Rate | Overbilled |
|---|---|---|---|---|
| CAR-000010 Bluecrest Freight Lines | 60 | 14 | 23.3% | $1,124.89 |
| CAR-000005 Silverline Logistics | 39 | 8 | 20.5% | $1,185.09 |
| CAR-000002 Brightwater Transport | 133 | 24 | 18.1% | $1,992.01 |
| CAR-000006 Riverbend Logistics | 86 | 12 | 14.0% | $974.18 |
| CAR-000009 Riverbend Haulage | 44 | 6 | 13.6% | $760.59 |
| CAR-000007 Falconridge Haulage | 46 | 6 | 13.0% | $1,288.97 |
| CAR-000001 Meadowlark Freight Lines | 204 | 22 | 10.8% | $4,604.83 |
| CAR-000008 Oakmont Carriers | 66 | 7 | 10.6% | $827.49 |
| CAR-000003 Oakmont Freight Lines | 45 | 4 | 8.9% | $1,357.71 |
| CAR-000004 Pinecone Haulage | 27 | 1 | 3.7% | $47.88 |

## Data validation

**PASS**: 168 of 168 checks passed.
