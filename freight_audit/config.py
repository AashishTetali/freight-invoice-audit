"""All tunable settings live here, so nothing "magic" is hidden in the code.

The same generator produces the small "starter" dataset and the "full"
dataset: only the numbers below change.
"""
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal

DEFAULT_SEED = 20261022

# How big each dataset is.
SCALES = {
    "starter": dict(carriers=10, shipments=1_000, invoices=750, lanes=40),
    "full": dict(carriers=100, shipments=20_000, invoices=15_000, lanes=300),
}

# Probability that an ORIGINAL invoice gets one deliberate error of this type.
# (Multiplied by a hidden per-carrier "sloppiness" factor, so some carriers
# make more mistakes than others - like in real life.)
ANOMALY_RATES = {
    "WEIGHT_TIER_MISAPPLIED": 0.025,
    "DIM_WEIGHT_DISCREPANCY": 0.025,
    "RATE_CARD_EXPIRED": 0.030,        # only possible for Jul-Sep shipments
    "FUEL_SURCHARGE_MISCALCULATED": 0.015,
    "SURCHARGE_DUPLICATED": 0.020,
    "INVALID_ACCESSORIAL": 0.030,
}


@dataclass(frozen=True)
class Config:
    seed: int = DEFAULT_SEED
    scale: str = "starter"
    carriers: int = 10
    shipments: int = 1_000
    invoices: int = 750
    lanes: int = 40
    year: int = 2026
    ship_start: date = date(2026, 1, 1)
    ship_end: date = date(2026, 9, 30)
    as_of: date = date(2026, 12, 31)          # "today" for statuses
    h2_start: date = date(2026, 7, 1)          # second rate-card period
    duplicate_invoice_rate: float = 0.015      # share of all invoices
    unknown_shipment_rate: float = 0.005       # share of all invoices
    anomaly_rates: dict = field(default_factory=lambda: dict(ANOMALY_RATES))
    min_cases_per_type: int = 3                # guarantee coverage even when small
    # Carrier behaviour when we dispute a charge
    credit_full_rate: float = 0.88
    credit_partial_rate: float = 0.05
    credit_decline_rate: float = 0.04          # remainder (~3%) = over-credit anomaly
    # Audit rules
    tolerance: Decimal = Decimal("0.01")       # differences <= 1 cent are ignored
    waive_below: Decimal = Decimal("5.00")     # not worth disputing below $5
    payment_terms_days: int = 30


def make_config(scale="starter", seed=DEFAULT_SEED, **overrides):
    if scale not in SCALES:
        raise ValueError(f"unknown scale {scale!r}; choose from {list(SCALES)}")
    base = Config(seed=seed, scale=scale, **SCALES[scale])
    clean = {k: v for k, v in overrides.items() if v is not None}
    return replace(base, **clean) if clean else base
