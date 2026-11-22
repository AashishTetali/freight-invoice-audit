"""The contract maths: what a shipment SHOULD cost.

    DIM weight      = ceil(L x W x H x pieces / dim_factor)
    billable weight = max(ceil(actual weight), DIM weight)
    base            = max(billable weight x tier rate, minimum charge)
    fuel            = base x fuel surcharge %
    accessorials    = contracted price x quantity performed (per delivery record)

Money uses Decimal (never float) and is rounded half-up to cents.
"""
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP, ROUND_CEILING

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def money(x):
    return Decimal(x).quantize(CENT, rounding=ROUND_HALF_UP)


def ceil_int(x):
    return int(Decimal(x).to_integral_value(rounding=ROUND_CEILING))


def dim_weight(length_in, width_in, height_in, pieces, dim_factor):
    cubic = length_in * width_in * height_in * pieces
    return -(-cubic // dim_factor)          # integer ceiling division


def billable_weight(actual_lb, dim_lb):
    return max(ceil_int(actual_lb), dim_lb)


def find_tier(tiers, weight):
    """tiers are rate_card_line rows sorted by tier_min_lb. Returns (index, row)."""
    for i, t in enumerate(tiers):
        if weight >= t["tier_min_lb"] and (t["tier_max_lb"] is None or weight <= t["tier_max_lb"]):
            return i, t
    raise ValueError(f"no tier for weight {weight}")


def base_charge(weight, rate, min_charge):
    raw = money(Decimal(weight) * rate)
    return (min_charge, 1) if raw < min_charge else (raw, 0)


def fuel_charge(base, pct):
    return money(base * pct / Decimal(100))


@dataclass
class Expected:
    shipment_id: str
    card: dict
    measurement: dict
    actual_lb: int
    dim_lb: int
    billable_lb: int
    tiers: list
    tier_index: int
    rate: Decimal
    base: Decimal
    min_applied: int
    fuel_pct: Decimal
    fuel: Decimal
    accessorials: list = field(default_factory=list)   # (code, qty, unit_rate, amount, unit)

    @property
    def total(self):
        return self.base + self.fuel + sum((a[3] for a in self.accessorials), ZERO)


class Index:
    """Fast look-ups over the tables, shared by the generator and the auditor."""

    def __init__(self, t):
        self.shipments = {s["shipment_id"]: s for s in t["shipment"]}
        self.cards = {c["rate_card_id"]: c for c in t["rate_card"]}
        self.cards_by_carrier = defaultdict(list)
        for c in t["rate_card"]:
            self.cards_by_carrier[c["carrier_id"]].append(c)
        for lst in self.cards_by_carrier.values():
            lst.sort(key=lambda c: c["effective_from"])
        self.tiers = defaultdict(list)
        for r in t["rate_card_line"]:
            self.tiers[(r["rate_card_id"], r["lane_id"], r["service_level"])].append(r)
        for lst in self.tiers.values():
            lst.sort(key=lambda r: r["tier_min_lb"])
        self.card_acc = defaultdict(dict)
        for r in t["rate_card_accessorial"]:
            self.card_acc[r["rate_card_id"]][r["accessorial_code"]] = r["contracted_amount"]
        self.catalog = {a["accessorial_code"]: a for a in t["accessorial"]}
        self.catalog_order = [a["accessorial_code"] for a in t["accessorial"]]
        self.measurements = defaultdict(list)
        for m in t["weight_measurement"]:
            self.measurements[m["shipment_id"]].append(m)
        self.delivery = {d["shipment_id"]: d for d in t["delivery_record"]}
        by_delivery = defaultdict(dict)
        for a in t["delivery_accessorial"]:
            by_delivery[a["delivery_id"]][a["accessorial_code"]] = a["quantity"]
        self.performed = {sid: by_delivery.get(d["delivery_id"], {})
                          for sid, d in self.delivery.items()}

    def card_for(self, carrier_id, on_date):
        for c in self.cards_by_carrier.get(carrier_id, []):
            if c["effective_from"] <= on_date <= c["effective_to"]:
                return c
        return None

    @staticmethod
    def is_effective(card, on_date):
        return card["effective_from"] <= on_date <= card["effective_to"]

    def authoritative_measurement(self, shipment_id):
        ms = self.measurements[shipment_id]
        reweighs = [m for m in ms if m["measurement_source"] == "CARRIER_REWEIGH"]
        pool = reweighs or ms
        return max(pool, key=lambda m: (m["measured_date"], m["measurement_id"]))

    def expected(self, shipment_id, card=None):
        s = self.shipments[shipment_id]
        card = card or self.card_for(s["carrier_id"], s["ship_date"])
        m = self.authoritative_measurement(shipment_id)
        dim = dim_weight(m["length_in"], m["width_in"], m["height_in"], m["pieces"],
                         card["dim_factor"])
        actual = ceil_int(m["actual_weight_lb"])
        billable = max(actual, dim)
        tiers = self.tiers[(card["rate_card_id"], s["lane_id"], s["service_level"])]
        idx, tier = find_tier(tiers, billable)
        base, min_applied = base_charge(billable, tier["rate_per_lb"], card["min_charge"])
        pct = card["fuel_surcharge_pct"]
        fuel = fuel_charge(base, pct)
        acc = []
        performed = self.performed.get(shipment_id, {})
        contract = self.card_acc[card["rate_card_id"]]
        for code in self.catalog_order:
            if code in performed and code in contract:
                qty = performed[code]
                unit = "HOUR" if self.catalog[code]["charge_basis"] == "PER_HOUR" else "EACH"
                acc.append((code, qty, contract[code], money(contract[code] * qty), unit))
        return Expected(shipment_id, card, m, actual, dim, billable, tiers, idx,
                        tier["rate_per_lb"], base, min_applied, pct, fuel, acc)
