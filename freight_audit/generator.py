"""Part A - the "world builder".

Creates the synthetic but realistic world, in dependency order:
    1. master data   : carriers, lanes, rate cards (+ tier prices), accessorials
    2. shipments     : legs, weight measurements, delivery records
    3. invoices      : correct bills first, then deliberately broken ones,
                       each break recorded in the ground truth (answer key)

Every random choice comes from random.Random seeded with "<seed>:<stream>", so
the same seed always produces exactly the same data.
"""
import copy
import math
import random
import string
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from . import reference as ref
from .pricing import Index, ZERO, base_charge, ceil_int, find_tier, fuel_charge, money
from .schema import TABLE_ORDER

ONE = Decimal("1")


def rng_for(cfg, stream):
    return random.Random(f"{cfg.seed}:{stream}")


def dec(x, places="0.0001"):
    return Decimal(str(x)).quantize(Decimal(places), rounding=ROUND_HALF_UP)


def miles_between(a, b):
    """Great-circle distance x 1.18 (roads are not straight)."""
    lat1, lon1, lat2, lon2 = map(math.radians, (a[2], a[3], b[2], b[3]))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return round(3958.8 * 2 * math.asin(math.sqrt(h)) * 1.18)


CITY = {c[0]: c for c in ref.CITIES}


# =================================================================== master data
def gen_master(cfg, t, hidden):
    r = rng_for(cfg, "master")
    names = [f"{p} {s}" for p in ref.CARRIER_PREFIXES for s in ref.CARRIER_SUFFIXES]
    if cfg.carriers > len(names):
        raise ValueError(f"at most {len(names)} carriers supported")
    r.shuffle(names)
    states = sorted({c[1] for c in ref.CITIES})
    scacs = set()
    first_day, last_day = date(2015, 1, 1), date(2025, 12, 1)
    carriers = []
    for i in range(cfg.carriers):
        while True:
            scac = "".join(r.choice(string.ascii_uppercase) for _ in range(4))
            if scac not in scacs:
                scacs.add(scac)
                break
        carriers.append(dict(
            carrier_id=f"CAR-{i + 1:06d}", carrier_name=names[i], scac_code=scac,
            hq_state=r.choice(states),
            onboarded_date=first_day + timedelta(days=r.randrange((last_day - first_day).days)),
            status="ACTIVE"))
    t["carrier"] = carriers
    ids = [c["carrier_id"] for c in carriers]

    # Hidden behaviour (never exported): size, sloppiness, billing delay.
    ranks = list(range(len(ids)))
    r.shuffle(ranks)
    size = {cid: 1 / (rank + 1) ** 0.8 for cid, rank in zip(ids, ranks)}
    raw = {cid: min(max(r.lognormvariate(0, 0.6), 0.2), 4.0) for cid in ids}
    mean = sum(raw.values()) / len(raw)
    hidden["size"] = size
    hidden["propensity"] = {cid: v / mean for cid, v in raw.items()}
    hidden["invoice_lag"] = {cid: r.randint(1, 10) for cid in ids}

    # Lanes
    pairs = [(a, b) for a in ref.CITIES for b in ref.CITIES if a[0] != b[0]]
    chosen = sorted(r.sample(pairs, min(cfg.lanes, len(pairs))), key=lambda p: (p[0][0], p[1][0]))
    t["lane"] = [dict(lane_id=f"LANE-{i + 1:06d}", origin_city=a[0], origin_state=a[1],
                      dest_city=b[0], dest_state=b[1], distance_miles=miles_between(a, b))
                 for i, (a, b) in enumerate(chosen)]
    lanes = t["lane"]

    # Accessorial catalogue
    t["accessorial"] = [dict(accessorial_code=code, description=d, charge_basis=basis,
                             reference_amount=amt)
                        for code, (d, basis, amt) in ref.ACCESSORIALS.items()]

    # Each carrier: lanes served, services offered, two rate cards.
    max_size = max(size.values())
    hidden["lanes"], hidden["services"] = {}, {}
    cards, lines, card_acc = [], [], []
    line_no = 0
    non_detention = [c for c in ref.ACCESSORIALS if c != "DETENTION"]
    for c in carriers:
        cid = c["carrier_id"]
        n = min(len(lanes), max(6, min(30, round(6 + 24 * size[cid] / max_size))))
        my_lanes = sorted(r.sample(lanes, n), key=lambda l: l["lane_id"])
        services = ["STANDARD"]
        if r.random() < 0.7:
            services.insert(0, "ECONOMY")
        if r.random() < 0.6:
            services.append("EXPEDITED")
        hidden["lanes"][cid], hidden["services"][cid] = my_lanes, services

        factor = dec(r.uniform(0.85, 1.15), "0.001")
        min_charge = money(r.randrange(70, 126))
        pct1 = dec(r.uniform(18, 30), "0.1")
        pct2 = pct1 - dec(r.uniform(0, 2), "0.1")
        dim_factor = r.choice([139, 139, 139, 166])
        excluded = set(r.sample(non_detention, r.choice([0, 1, 1, 2])))
        acc_prices = {code: money(round(float(amt) * r.uniform(0.8, 1.2)))
                      for code, (_, _, amt) in ref.ACCESSORIALS.items() if code not in excluded}

        id1, id2 = f"RATE-{cid}-{cfg.year}-01", f"RATE-{cid}-{cfg.year}-02"
        periods = [(id1, "2026-01", date(cfg.year, 1, 1), cfg.h2_start - timedelta(days=1), None, pct1),
                   (id2, "2026-02", cfg.h2_start, date(cfg.year, 12, 31), id1, pct2)]
        for rid, version, start, end, prev, pct in periods:
            cards.append(dict(rate_card_id=rid, carrier_id=cid, version=version,
                              effective_from=start, effective_to=end, previous_rate_card_id=prev,
                              currency="USD", min_charge=min_charge, fuel_surcharge_pct=pct,
                              dim_factor=dim_factor,
                              status="EXPIRED" if end < cfg.as_of else "ACTIVE"))
            for code in ref.ACCESSORIALS:
                if code in acc_prices:
                    card_acc.append(dict(rate_card_id=rid, accessorial_code=code,
                                         contracted_amount=acc_prices[code]))
        # Tier prices: cheaper per lb for heavier freight, longer lanes cost more.
        h1_rows, h2_rows = [], []
        for lane in my_lanes:
            noise = dec(r.uniform(0.95, 1.05), "0.001")
            for svc in services:
                base0 = ((Decimal("0.06") + Decimal("0.00011") * lane["distance_miles"])
                         * factor * ref.SERVICE_MULTIPLIER[svc] * noise)
                for tmin, tmax, mult in ref.WEIGHT_TIERS:
                    rate1 = (base0 * mult).quantize(Decimal("0.0001"), ROUND_HALF_UP)
                    rate2 = (rate1 * ref.H2_RATE_FACTOR).quantize(Decimal("0.0001"), ROUND_HALF_UP)
                    common = dict(lane_id=lane["lane_id"], service_level=svc,
                                  tier_min_lb=tmin, tier_max_lb=tmax)
                    h1_rows.append(dict(rate_card_id=id1, rate_per_lb=rate1, **common))
                    h2_rows.append(dict(rate_card_id=id2, rate_per_lb=rate2, **common))
        for row in h1_rows + h2_rows:
            line_no += 1
            lines.append(dict(rate_line_id=f"RLN-{line_no:06d}", **row))
    t["rate_card"], t["rate_card_line"], t["rate_card_accessorial"] = cards, lines, card_acc


# =================================================================== shipments
MONTH_WEIGHTS = [0.80, 0.85, 1.00, 1.00, 1.05, 1.05, 0.95, 1.00, 1.15, 1.10, 1.20, 1.25]
SERVICE_WEIGHTS = {"ECONOMY": 25, "STANDARD": 60, "EXPEDITED": 15}


def _random_ship_date(r, cfg):
    months = list(range(cfg.ship_start.month, cfg.ship_end.month + 1))
    m = r.choices(months, [MONTH_WEIGHTS[x - 1] for x in months])[0]
    first = date(cfg.year, m, 1)
    nxt = date(cfg.year + (m == 12), m % 12 + 1, 1)
    d = first + timedelta(days=r.randrange((nxt - first).days))
    return min(max(d, cfg.ship_start), cfg.ship_end)


def _route(r, origin, dest):
    """Direct, or via one or two hubs if the detour is reasonable."""
    direct = miles_between(origin, dest)
    n_legs = r.choices([1, 2, 3], [60, 30, 10])[0]
    if n_legs == 1:
        return [origin, dest]
    hubs = [CITY[h] for h in ref.HUBS if h not in (origin[0], dest[0])]
    one = [h for h in hubs if miles_between(origin, h) + miles_between(h, dest) <= 1.35 * direct]
    if not one:
        return [origin, dest]
    h1 = r.choice(one)
    if n_legs == 3:
        two = [h for h in hubs if h is not h1 and miles_between(origin, h1) + miles_between(h1, h)
               + miles_between(h, dest) <= 1.5 * direct]
        if two:
            return [origin, h1, r.choice(two), dest]
    return [origin, h1, dest]


def gen_shipments(cfg, t, hidden):
    r = rng_for(cfg, "shipments")
    carrier_ids = [c["carrier_id"] for c in t["carrier"]]
    weights = [hidden["size"][c] for c in carrier_ids]
    drafts = []
    for _ in range(cfg.shipments):
        cid = r.choices(carrier_ids, weights)[0]
        lane = r.choice(hidden["lanes"][cid])
        services = hidden["services"][cid]
        svc = r.choices(services, [SERVICE_WEIGHTS[s] for s in services])[0]
        ship_date = _random_ship_date(r, cfg)
        pieces = r.choices([1, 2, 3, 4, 5, 6], [40, 25, 15, 10, 6, 4])[0]
        if r.random() < 0.8:
            length, width = 48, 40                       # standard pallet
        else:
            length, width = r.randint(30, 72), r.randint(24, 48)   # crate
        height = r.randint(24, 84)
        density = min(max(r.lognormvariate(math.log(16), 0.55), 2.0), 40.0)   # lb per cu ft
        cuft = length * width * height * pieces / 1728
        weight = max(15.0, min(round(cuft * density, 1), 15000.0))
        drafts.append(dict(carrier_id=cid, lane=lane, service_level=svc, ship_date=ship_date,
                           pieces=pieces, dims=(length, width, height), weight=weight, cuft=cuft))
    drafts.sort(key=lambda d: (d["ship_date"], d["carrier_id"], d["lane"]["lane_id"]))

    card_by_carrier = {}
    for c in t["rate_card"]:
        card_by_carrier.setdefault(c["carrier_id"], []).append(c)
    acc_by_card = {}
    for a in t["rate_card_accessorial"]:
        acc_by_card.setdefault(a["rate_card_id"], set()).add(a["accessorial_code"])

    r = rng_for(cfg, "shipment_details")
    shipments, legs, meas, deliveries, dacc, hist = [], [], [], [], [], []
    m_no = 0
    for i, d in enumerate(drafts, 1):
        sid = f"SHP-{cfg.year}-{i:06d}"
        lane, svc, ship = d["lane"], d["service_level"], d["ship_date"]
        speed = ref.SERVICE_SPEED_MILES_PER_DAY[svc]
        origin, dest = CITY[lane["origin_city"]], CITY[lane["dest_city"]]
        path = _route(r, origin, dest)
        cur = ship
        for n, (a, b) in enumerate(zip(path, path[1:]), 1):
            miles = miles_between(a, b)
            days = max(1, math.ceil(miles / speed)) + (1 if svc == "ECONOMY" else 0)
            arrive = cur + timedelta(days=days)
            legs.append(dict(leg_id=f"{sid}-L{n}", shipment_id=sid, leg_seq=n,
                             origin_city=a[0], origin_state=a[1], dest_city=b[0],
                             dest_state=b[1], leg_miles=miles, depart_date=cur,
                             arrive_date=arrive))
            cur = arrive + timedelta(days=0 if svc == "EXPEDITED" else 1)
        if r.random() < 0.10:                                   # late delivery
            legs[-1]["arrive_date"] += timedelta(days=r.randint(1, 3))
        delivered = legs[-1]["arrive_date"]
        buffer = {"EXPEDITED": 1, "STANDARD": 2, "ECONOMY": 4}[svc]     # hub handling time
        planned = ship + timedelta(days=math.ceil(lane["distance_miles"] / speed) + buffer)
        length, width, height = d["dims"]
        shipments.append(dict(shipment_id=sid, carrier_id=d["carrier_id"], lane_id=lane["lane_id"],
                              service_level=svc, ship_date=ship, planned_delivery_date=planned,
                              pieces=d["pieces"], current_status=None))
        m_no += 1
        meas.append(dict(measurement_id=f"WM-{cfg.year}-{m_no:06d}", shipment_id=sid,
                         measurement_source="SHIPPER_BOL", measured_by_role="SUPPLIER",
                         measured_date=ship, pieces=d["pieces"], length_in=length,
                         width_in=width, height_in=height,
                         actual_weight_lb=dec(d["weight"], "0.1")))
        if r.random() < 0.30:                                   # carrier re-weighs it
            new_h = height
            if r.random() < 0.2:
                new_h = max(12, height + r.choice([-1, 1]) * r.randint(2, 6))
            m_no += 1
            meas.append(dict(measurement_id=f"WM-{cfg.year}-{m_no:06d}", shipment_id=sid,
                             measurement_source="CARRIER_REWEIGH", measured_by_role="CARRIER",
                             measured_date=min(ship + timedelta(days=1), delivered),
                             pieces=d["pieces"], length_in=length, width_in=width,
                             height_in=new_h,
                             actual_weight_lb=dec(d["weight"] * r.uniform(0.97, 1.08), "0.1")))
        # Delivery record and services actually performed
        u = r.random()
        wait = r.uniform(0, 1) if u < 0.70 else r.uniform(1, 2) if u < 0.92 else r.uniform(2.5, 5)
        wait = dec(round(wait * 2) / 2, "0.01")
        did = f"DLV-{cfg.year}-{i:06d}"
        deliveries.append(dict(delivery_id=did, shipment_id=sid, delivered_date=delivered,
                               delivery_outcome="ON_TIME" if delivered <= planned else "LATE",
                               wait_hours=wait, recorded_by_role="WAREHOUSE_OPERATOR"))
        card = next(c for c in card_by_carrier[d["carrier_id"]]
                    if c["effective_from"] <= ship <= c["effective_to"])
        allowed = acc_by_card[card["rate_card_id"]]
        for code in ref.ACCESSORIALS:
            if code not in allowed:
                continue
            if code == "DETENTION":
                if wait > ref.DETENTION_FREE_HOURS:
                    dacc.append(dict(delivery_id=did, accessorial_code=code,
                                     quantity=wait - ref.DETENTION_FREE_HOURS))
            elif r.random() < ref.PERFORM_PROBABILITY[code]:
                dacc.append(dict(delivery_id=did, accessorial_code=code, quantity=dec(1, "0.01")))
        booked = ship - timedelta(days=r.randint(1, 5))
        for frm, to, when, actor in [(None, "BOOKED", booked, "PLANNER"),
                                     ("BOOKED", "PICKED_UP", ship, "CARRIER"),
                                     ("PICKED_UP", "IN_TRANSIT", ship, "CARRIER"),
                                     ("IN_TRANSIT", "DELIVERED", delivered, "WAREHOUSE_OPERATOR")]:
            hist.append(dict(event_id=None, shipment_id=sid, from_status=frm, to_status=to,
                             event_date=when, actor_role=actor))
    t["shipment"], t["shipment_leg"], t["weight_measurement"] = shipments, legs, meas
    t["delivery_record"], t["delivery_accessorial"] = deliveries, dacc
    t["shipment_status_history"] = hist


# =================================================================== invoices
def _clean_lines(e):
    lines = [dict(charge_type="BASE", accessorial_code=None, quantity=Decimal(e.billable_lb),
                  unit="LB", unit_rate=e.rate, min_charge_applied=e.min_applied, amount=e.base),
             dict(charge_type="FUEL", accessorial_code=None, quantity=e.base, unit="PCT_OF_BASE",
                  unit_rate=e.fuel_pct, min_charge_applied=0, amount=e.fuel)]
    for code, qty, rate, amount, unit in e.accessorials:
        lines.append(dict(charge_type="ACCESSORIAL", accessorial_code=code, quantity=qty,
                          unit=unit, unit_rate=rate, min_charge_applied=0, amount=amount))
    return lines


def _total(lines):
    return sum((l["amount"] for l in lines), ZERO)


def _tier_text(t):
    return f"{t['tier_min_lb']}+ lb" if t["tier_max_lb"] is None else \
        f"{t['tier_min_lb']}-{t['tier_max_lb']} lb"


def _label(d, issue, line_no, line_variance, detail):
    d["label"] = dict(issue_type=issue, line_no=line_no, line_variance=line_variance,
                      detail=detail)
    return True


def inj_weight_tier(d, idx, r, cfg):
    e = d["exp"]
    if e.tier_index == 0:
        return False
    wrong = e.tiers[e.tier_index - 1]                      # lighter tier = higher price
    base, min_applied = base_charge(e.billable_lb, wrong["rate_per_lb"], e.card["min_charge"])
    if base - e.base < ONE:
        return False
    b, f = d["lines"][0], d["lines"][1]
    b.update(unit_rate=wrong["rate_per_lb"], amount=base, min_charge_applied=min_applied)
    f.update(quantity=base, amount=fuel_charge(base, e.fuel_pct))
    return _label(d, "WEIGHT_TIER_MISAPPLIED", 1, base - e.base,
                  f"{e.billable_lb} lb priced at the {_tier_text(wrong)} rate "
                  f"{wrong['rate_per_lb']} instead of the {_tier_text(e.tiers[e.tier_index])} "
                  f"rate {e.rate}")


def inj_dim_weight(d, idx, r, cfg):
    e = d["exp"]
    new_w = ceil_int(Decimal(e.billable_lb) * dec(r.uniform(1.15, 1.6), "0.01"))
    _, tier = find_tier(e.tiers, new_w)
    base, min_applied = base_charge(new_w, tier["rate_per_lb"], e.card["min_charge"])
    if base - e.base < ONE:
        return False
    b, f = d["lines"][0], d["lines"][1]
    b.update(quantity=Decimal(new_w), unit_rate=tier["rate_per_lb"], amount=base,
             min_charge_applied=min_applied)
    f.update(quantity=base, amount=fuel_charge(base, e.fuel_pct))
    d["billed_weight_lb"] = new_w
    return _label(d, "DIM_WEIGHT_DISCREPANCY", 1, base - e.base,
                  f"Billed {new_w} lb (inflated dimensional weight) but measurements support "
                  f"{e.billable_lb} lb")


def inj_rate_card_expired(d, idx, r, cfg):
    e, s = d["exp"], d["ship"]
    if s["ship_date"] < cfg.h2_start or not e.card["previous_rate_card_id"]:
        return False
    old = idx.cards[e.card["previous_rate_card_id"]]
    eo = idx.expected(s["shipment_id"], card=old)
    lines = _clean_lines(eo)
    if _total(lines) - e.total < ONE:
        return False
    d.update(lines=lines, rate_card_id=old["rate_card_id"], billed_weight_lb=eo.billable_lb)
    return _label(d, "RATE_CARD_EXPIRED", 1, eo.base - e.base,
                  f"Billed with {old['rate_card_id']} (expired {old['effective_to']}) for a "
                  f"shipment dated {s['ship_date']}")


def inj_fuel(d, idx, r, cfg):
    e = d["exp"]
    wrong_pct = e.fuel_pct + Decimal(r.choice([2, 3, 4, 5, 6]))
    fuel = fuel_charge(e.base, wrong_pct)
    if fuel - e.fuel < ONE:
        return False
    d["lines"][1].update(unit_rate=wrong_pct, amount=fuel)
    return _label(d, "FUEL_SURCHARGE_MISCALCULATED", 2, fuel - e.fuel,
                  f"Fuel billed at {wrong_pct}% instead of the contracted {e.fuel_pct}%")


def inj_duplicate_surcharge(d, idx, r, cfg):
    lines = d["lines"]
    acc = [l for l in lines if l["charge_type"] == "ACCESSORIAL"]
    src = r.choice(acc) if acc and r.random() < 0.4 else lines[1]
    lines.append(dict(src))
    what = src["accessorial_code"] or "Fuel surcharge"
    return _label(d, "SURCHARGE_DUPLICATED", len(lines), src["amount"],
                  f"{what} line {lines.index(src) + 1} billed a second time as line {len(lines)}")


def inj_invalid_accessorial(d, idx, r, cfg):
    e = d["exp"]
    sid = d["ship"]["shipment_id"]
    performed = idx.performed.get(sid, {})
    contract = idx.card_acc[e.card["rate_card_id"]]
    not_performed = [c for c in idx.catalog_order if c in contract and c not in performed]
    not_contracted = [c for c in idx.catalog_order if c not in contract]
    if not_contracted and (r.random() < 0.4 or not not_performed):
        code = r.choice(not_contracted)
        price, why = idx.catalog[code]["reference_amount"], "is not allowed by the rate card"
    elif not_performed:
        code = r.choice(not_performed)
        price, why = contract[code], "was not performed per the delivery record"
    else:
        return False
    per_hour = idx.catalog[code]["charge_basis"] == "PER_HOUR"
    qty = dec(r.choice([1, 1.5, 2]), "0.01") if per_hour else dec(1, "0.01")
    amount = money(price * qty)
    d["lines"].append(dict(charge_type="ACCESSORIAL", accessorial_code=code, quantity=qty,
                           unit="HOUR" if per_hour else "EACH", unit_rate=price,
                           min_charge_applied=0, amount=amount))
    return _label(d, "INVALID_ACCESSORIAL", len(d["lines"]), amount, f"{code} billed but {why}")


INJECTORS = {
    "WEIGHT_TIER_MISAPPLIED": inj_weight_tier,
    "DIM_WEIGHT_DISCREPANCY": inj_dim_weight,
    "RATE_CARD_EXPIRED": inj_rate_card_expired,
    "FUEL_SURCHARGE_MISCALCULATED": inj_fuel,
    "SURCHARGE_DUPLICATED": inj_duplicate_surcharge,
    "INVALID_ACCESSORIAL": inj_invalid_accessorial,
}


def _try_inject(issue, d, idx, r, cfg):
    """Injectors only change the draft when they succeed."""
    backup = copy.deepcopy({k: d[k] for k in ("lines", "rate_card_id", "billed_weight_lb")})
    if INJECTORS[issue](d, idx, r, cfg):
        return True
    d.update(backup)
    d["label"] = None
    return False


def gen_invoices(cfg, t, hidden):
    idx = Index(t)
    r = rng_for(cfg, "invoices")
    n_dup = max(cfg.min_cases_per_type, round(cfg.invoices * cfg.duplicate_invoice_rate))
    n_unknown = max(cfg.min_cases_per_type, round(cfg.invoices * cfg.unknown_shipment_rate))
    n_orig = cfg.invoices - n_dup - n_unknown
    if n_orig > len(t["shipment"]) or n_orig <= 0:
        raise ValueError("invoices must be fewer than shipments (plus duplicates/unknowns)")

    deliveries = idx.delivery
    drafts = []
    for i in sorted(r.sample(range(len(t["shipment"])), n_orig)):
        s = t["shipment"][i]
        e = idx.expected(s["shipment_id"])
        inv_date = (deliveries[s["shipment_id"]]["delivered_date"]
                    + timedelta(days=hidden["invoice_lag"][s["carrier_id"]] + r.randint(0, 4)))
        drafts.append(dict(kind="ORIGINAL", carrier_id=s["carrier_id"], ship=s, exp=e,
                           shipment_ref=s["shipment_id"], rate_card_id=e.card["rate_card_id"],
                           invoice_date=inv_date, billed_weight_lb=e.billable_lb,
                           lines=_clean_lines(e), label=None))

    # 1) Random errors, more likely for sloppy carriers
    issues = list(cfg.anomaly_rates)
    for d in drafts:
        p = hidden["propensity"][d["carrier_id"]]
        u, acc = r.random(), 0.0
        for issue in issues:
            acc += cfg.anomaly_rates[issue] * p
            if u < acc:
                _try_inject(issue, d, idx, r, cfg)
                break
    # 2) Guarantee a minimum number of every case (matters for small datasets)
    order = list(range(len(drafts)))
    r.shuffle(order)
    for issue in issues:
        have = sum(1 for d in drafts if d["label"] and d["label"]["issue_type"] == issue)
        for k in order:
            if have >= cfg.min_cases_per_type:
                break
            if drafts[k]["label"] is None and _try_inject(issue, drafts[k], idx, r, cfg):
                have += 1

    # 3) Duplicate bills: the same clean invoice sent again weeks later
    clean = [d for d in drafts if d["label"] is None]
    dups = []
    for d in sorted(r.sample(clean, min(n_dup, len(clean))), key=lambda x: x["shipment_ref"]):
        later = min(d["invoice_date"] + timedelta(days=r.randint(20, 45)),
                    cfg.as_of - timedelta(days=2))
        dup = dict(d, kind="DUPLICATE", invoice_date=later, lines=copy.deepcopy(d["lines"]),
                   original=d)
        dup["label"] = dict(issue_type="DUPLICATE_INVOICE", line_no=None,
                            line_variance=_total(d["lines"]), detail=None)
        dups.append(dup)

    # 4) Bills for shipments that do not exist
    unknowns, used = [], set()
    carrier_ids = [c["carrier_id"] for c in t["carrier"]]
    for _ in range(n_unknown):
        cid = r.choices(carrier_ids, [hidden["size"][c] for c in carrier_ids])[0]
        lane = r.choice(hidden["lanes"][cid])
        ship = _random_ship_date(r, cfg)
        card = idx.card_for(cid, ship)
        tiers = idx.tiers[(card["rate_card_id"], lane["lane_id"], "STANDARD")]
        weight = r.randint(150, 4000)
        _, tier = find_tier(tiers, weight)
        base, min_applied = base_charge(weight, tier["rate_per_lb"], card["min_charge"])
        while True:
            num = r.randint(900001, 999999)
            if num not in used:
                used.add(num)
                break
        ref_id = f"SHP-{cfg.year}-{num:06d}"
        lines = [dict(charge_type="BASE", accessorial_code=None, quantity=Decimal(weight),
                      unit="LB", unit_rate=tier["rate_per_lb"], min_charge_applied=min_applied,
                      amount=base),
                 dict(charge_type="FUEL", accessorial_code=None, quantity=base,
                      unit="PCT_OF_BASE", unit_rate=card["fuel_surcharge_pct"],
                      min_charge_applied=0, amount=fuel_charge(base, card["fuel_surcharge_pct"]))]
        unknowns.append(dict(kind="UNKNOWN", carrier_id=cid, ship=None, exp=None,
                             shipment_ref=ref_id, rate_card_id=card["rate_card_id"],
                             invoice_date=ship + timedelta(days=r.randint(3, 15)),
                             billed_weight_lb=weight, lines=lines,
                             label=dict(issue_type="UNKNOWN_SHIPMENT", line_no=None,
                                        line_variance=_total(lines),
                                        detail=f"Shipment {ref_id} does not exist in shipment "
                                               f"records")))

    # 5) Number invoices in the order they were received
    rank = {"ORIGINAL": 0, "DUPLICATE": 1, "UNKNOWN": 2}
    everything = sorted(drafts + dups + unknowns,
                        key=lambda d: (d["invoice_date"], d["shipment_ref"], rank[d["kind"]]))
    invoices, lines_out, hist, gt = [], [], [], []
    for n, d in enumerate(everything, 1):
        inv_id = f"FINV-{cfg.year}-{n:06d}"
        d["invoice_id"] = inv_id
        total = _total(d["lines"])
        invoices.append(dict(invoice_id=inv_id, carrier_id=d["carrier_id"],
                             shipment_ref=d["shipment_ref"], rate_card_id=d["rate_card_id"],
                             invoice_date=d["invoice_date"],
                             billed_weight_lb=d["billed_weight_lb"], total_billed=total,
                             currency="USD", current_status=None))
        for k, l in enumerate(d["lines"], 1):
            lines_out.append(dict(line_id=f"FLINE-{inv_id}-{k:02d}", invoice_id=inv_id,
                                  line_no=k, **l))
        hist.append(dict(event_id=None, invoice_id=inv_id, from_status=None,
                         to_status="RECEIVED", event_date=d["invoice_date"], actor_role="CARRIER",
                         note="Invoice submitted by carrier"))
    for d in everything:
        lab = d["label"]
        if not lab:
            continue
        if d["kind"] == "DUPLICATE":
            lab["detail"] = (f"Second bill for shipment {d['shipment_ref']}; already billed on "
                             f"{d['original']['invoice_id']}")
        if d["kind"] in ("DUPLICATE", "UNKNOWN"):
            decision, variance = "REJECT_INVOICE", _total(d["lines"])
        else:
            variance = _total(d["lines"]) - d["exp"].total
            decision = "DISPUTE" if lab["line_variance"] >= cfg.waive_below else "WAIVE"
        gt.append(dict(gt_id=None, entity_type="INVOICE", entity_id=d["invoice_id"],
                       issue_type=lab["issue_type"], line_no=lab["line_no"],
                       expected_decision=decision, expected_variance_amount=variance,
                       injection_detail=lab["detail"]))
    t["freight_invoice"], t["invoice_line"], t["invoice_status_history"] = invoices, lines_out, hist
    return gt


def generate(cfg):
    """Returns (tables, ground_truth_rows, hidden_parameters)."""
    t = {name: [] for name in TABLE_ORDER}
    hidden = {}
    gen_master(cfg, t, hidden)
    gen_shipments(cfg, t, hidden)
    gt = gen_invoices(cfg, t, hidden)
    return t, gt, hidden
