# Freight invoice audit - results (full, seed 20261022)

## What happened

* 15,000 carrier invoices were audited against contracts and delivery records (20,000 shipments in total).
* 12,786 were correct (85.2%), 1,914 had line-level problems and 300 were rejected outright (duplicates / unknown shipments).
* Total billed $6,740,996.96. Rejected bills worth $122,749.48; line overbilling found $145,040.66; disputed $144,263.43; credits applied $131,255.00; over-limit credits held $5,154.83.
* Money saved (rejected bills + credits): **$254,004.48** (3.8% of billed).

## How well the audit engine found the planted problems

Overall precision **100.0%**, recall **100.0%**; false alarms on clean invoices: 0 of 12,786. Decisions matching the rules: 100.0%; $ variance matching the answer key: 100.0%.

| Issue type | Planted | Detected | Precision | Recall |
|---|---|---|---|---|
| UNKNOWN_SHIPMENT | 75 | 75 | 100.0% | 100.0% |
| DUPLICATE_INVOICE | 225 | 225 | 100.0% | 100.0% |
| RATE_CARD_EXPIRED | 165 | 165 | 100.0% | 100.0% |
| WEIGHT_TIER_MISAPPLIED | 360 | 360 | 100.0% | 100.0% |
| DIM_WEIGHT_DISCREPANCY | 368 | 368 | 100.0% | 100.0% |
| FUEL_SURCHARGE_MISCALCULATED | 240 | 240 | 100.0% | 100.0% |
| SURCHARGE_DUPLICATED | 301 | 301 | 100.0% | 100.0% |
| INVALID_ACCESSORIAL | 480 | 480 | 100.0% | 100.0% |
| CREDIT_EXCEEDS_EXCEPTION | 68 | 68 | 100.0% | 100.0% |

## Exceptions raised

| Type | Count | Total variance |
|---|---|---|
| CREDIT_EXCEEDS_EXCEPTION | 68 | $1,186.86 |
| DIM_WEIGHT_DISCREPANCY | 368 | $40,423.84 |
| DUPLICATE_INVOICE | 225 | $96,237.63 |
| FUEL_ON_INCORRECT_BASE | 893 | $16,864.27 |
| FUEL_SURCHARGE_MISCALCULATED | 240 | $2,849.26 |
| INVALID_ACCESSORIAL | 480 | $34,031.00 |
| RATE_CARD_EXPIRED | 165 | $2,228.55 |
| SURCHARGE_DUPLICATED | 301 | $22,998.84 |
| UNKNOWN_SHIPMENT | 75 | $26,511.85 |
| WEIGHT_TIER_MISAPPLIED | 360 | $25,644.90 |

## Example exceptions (one per type)

* `FEX-2026-000001` **INVALID_ACCESSORIAL** on `FLINE-FINV-2026-000014-05`: LIMITED_ACCESS billed but not recorded on delivery DLV-2026-000164 (variance $71.00)
* `FEX-2026-000003` **DIM_WEIGHT_DISCREPANCY** on `FLINE-FINV-2026-000025-01`: Billed 6270 lb; measurements give actual 4071 lb, DIM 2256 lb (factor 166), so billable is 4071 lb (variance $83.34)
* `FEX-2026-000004` **FUEL_ON_INCORRECT_BASE** on `FLINE-FINV-2026-000025-02`: Fuel 19.3% applied to the incorrect base $378.08; correct is 19.3% of $294.74 = $56.88 (knock-on of FEX-2026-000003) (variance $16.09)
* `FEX-2026-000005` **SURCHARGE_DUPLICATED** on `FLINE-FINV-2026-000054-03`: Fuel surcharge billed again (duplicate of line 2) (variance $74.84)
* `FEX-2026-000013` **WEIGHT_TIER_MISAPPLIED** on `FLINE-FINV-2026-000096-01`: 4559 lb belongs in tier 2000-4999 lb at 0.0615/lb, but the 1000-1999 lb rate 0.0738/lb was used (variance $56.07)
* `FEX-2026-000019` **FUEL_SURCHARGE_MISCALCULATED** on `FLINE-FINV-2026-000119-02`: Fuel billed at 28.7% = $30.33; contract is 25.7% of $105.69 = $27.16 (variance $3.17)
* `FEX-2026-000032` **UNKNOWN_SHIPMENT** on `FINV-2026-000185`: Invoice quotes shipment SHP-2026-935979, which does not exist (variance $173.28)
* `FEX-2026-000120` **DUPLICATE_INVOICE** on `FINV-2026-000626`: Shipment SHP-2026-000007 was already billed on FINV-2026-000001 (identical amount $145.88) (variance $145.88)
* `FEX-2026-001733` **RATE_CARD_EXPIRED** on `FLINE-FINV-2026-009405-01`: Billed with RATE-CAR-000080-2026-01 valid 2026-01-01 to 2026-06-30; ship date 2026-07-02 requires RATE-CAR-000080-2026-02 (variance $0.00)
* `FEX-2026-003108` **CREDIT_EXCEEDS_EXCEPTION** on `FLINE-FINV-2026-000402-03`: Credit note CN-2026-000037 for $71.82 exceeds the $63.00 approved on FEX-2026-000069 (variance $8.82)

## Carriers with the highest exception rate

| Carrier | Invoices | With exceptions | Rate | Overbilled |
|---|---|---|---|---|
| CAR-000061 Silverline Express | 321 | 116 | 36.1% | $13,073.92 |
| CAR-000043 Driftwood Express | 46 | 14 | 30.4% | $1,146.36 |
| CAR-000082 Silverline Transport | 492 | 146 | 29.7% | $16,453.72 |
| CAR-000087 Oakmont Logistics | 82 | 23 | 28.1% | $2,393.24 |
| CAR-000049 Stonebridge Haulage | 58 | 15 | 25.9% | $1,527.81 |
| CAR-000097 Highplain Cartage | 81 | 20 | 24.7% | $1,287.99 |
| CAR-000088 Riverbend Transport | 1863 | 457 | 24.5% | $46,813.74 |
| CAR-000025 Sunvale Transport | 37 | 9 | 24.3% | $1,150.46 |
| CAR-000054 Thistle Logistics | 110 | 26 | 23.6% | $2,606.41 |
| CAR-000079 Harborview Cartage | 182 | 43 | 23.6% | $4,881.43 |

## Data validation

**PASS**: 168 of 168 checks passed.
