"""Data-quality checks. Each check reports PASS/FAIL, how many rows it looked at
and a few examples of failures, so problems are easy to find."""
import re
from collections import Counter, defaultdict
from decimal import Decimal

from .pricing import ZERO, Index, money
from .schema import GROUND_TRUTH, TABLES, TRANSITIONS

MAX_EXAMPLES = 5


class Report:
    def __init__(self):
        self.checks = []

    def add(self, name, category, checked, failures):
        failures = list(failures)
        self.checks.append(dict(name=name, category=category,
                                status="PASS" if not failures else "FAIL", checked=checked,
                                failed=len(failures), examples=failures[:MAX_EXAMPLES]))

    def summary(self):
        failed = [c for c in self.checks if c["status"] == "FAIL"]
        return dict(overall="PASS" if not failed else "FAIL", checks_run=len(self.checks),
                    checks_passed=len(self.checks) - len(failed), checks_failed=len(failed),
                    checks=self.checks)


def _key(row, cols):
    return tuple(row[c] for c in cols)


def schema_checks(rep, t, gt):
    tables = list(TABLES) + [GROUND_TRUTH]
    data = dict(t, ground_truth=gt)
    keys = {}
    for tb in tables:
        keys[tb.name] = {c.name: {r[c.name] for r in data[tb.name]} for c in tb.cols}
    for tb in tables:
        rows = data[tb.name]
        n = len(rows)
        pk = Counter(_key(r, tb.pk) for r in rows)
        rep.add(f"{tb.name}: primary key {'+'.join(tb.pk)} is unique", "primary_key", n,
                [f"{k} x{v}" for k, v in pk.items() if v > 1])
        for cols in tb.unique_together:
            cnt = Counter(_key(r, cols) for r in rows)
            rep.add(f"{tb.name}: ({', '.join(cols)}) is unique", "duplicates", n,
                    [f"{k} x{v}" for k, v in cnt.items() if v > 1])
        req, enum, pat, uniq = [], [], [], []
        for c in tb.cols:
            rx = re.compile(c.pattern) if c.pattern else None
            seen = Counter()
            for r in rows:
                v = r.get(c.name)
                if v is None:
                    if c.required:
                        req.append(f"{_key(r, tb.pk)}.{c.name} is empty")
                    continue
                if c.enum is not None and v not in c.enum:
                    enum.append(f"{_key(r, tb.pk)}.{c.name}={v!r}")
                if rx and not rx.match(str(v)):
                    pat.append(f"{_key(r, tb.pk)}.{c.name}={v!r}")
                if c.unique:
                    seen[v] += 1
            uniq += [f"{c.name}={v!r} x{k}" for v, k in seen.items() if k > 1]
        rep.add(f"{tb.name}: required fields present", "required", n, req)
        rep.add(f"{tb.name}: values in allowed lists", "enum", n, enum)
        rep.add(f"{tb.name}: ID / code formats", "id_format", n, pat)
        rep.add(f"{tb.name}: unique columns", "duplicates", n, uniq)
        for c in tb.cols:
            if not c.fk:
                continue
            ptable, pcol = c.fk.split(".")
            parents = keys[ptable][pcol]
            bad = [f"{_key(r, tb.pk)}.{c.name}={r[c.name]}" for r in rows
                   if r.get(c.name) is not None and r[c.name] not in parents]
            rep.add(f"{tb.name}.{c.name} -> {c.fk}", "foreign_key", n, bad)


def business_checks(rep, t, gt, cfg):
    idx = Index(t)
    ships = idx.shipments
    lanes = {l["lane_id"]: l for l in t["lane"]}
    carriers = {c["carrier_id"]: c for c in t["carrier"]}

    # ---------------- master data
    bad = []
    for cid, cards in idx.cards_by_carrier.items():
        for c in cards:
            if c["effective_from"] > c["effective_to"]:
                bad.append(f"{c['rate_card_id']} starts after it ends")
            if carriers[cid]["onboarded_date"] >= c["effective_from"]:
                bad.append(f"{cid} onboarded after {c['rate_card_id']} starts")
        for a, b in zip(cards, cards[1:]):
            if (b["effective_from"] - a["effective_to"]).days != 1:
                bad.append(f"gap/overlap between {a['rate_card_id']} and {b['rate_card_id']}")
            if b["previous_rate_card_id"] != a["rate_card_id"]:
                bad.append(f"{b['rate_card_id']} does not point to {a['rate_card_id']}")
    rep.add("Rate cards: effective dates ordered, no gaps or overlaps", "date_order",
            len(t["rate_card"]), bad)
    bad = []
    for key, tiers in idx.tiers.items():
        if tiers[0]["tier_min_lb"] != 0 or tiers[-1]["tier_max_lb"] is not None:
            bad.append(f"{key} tiers do not cover 0..unlimited")
        for a, b in zip(tiers, tiers[1:]):
            if a["tier_max_lb"] + 1 != b["tier_min_lb"]:
                bad.append(f"{key} gap between tiers")
            if not b["rate_per_lb"] < a["rate_per_lb"]:
                bad.append(f"{key} heavier tier is not cheaper")
    rep.add("Rate tiers: contiguous, cover all weights, cheaper when heavier", "arithmetic",
            len(idx.tiers), bad)
    rep.add("Lanes: origin differs from destination, distance > 0", "allowed_values",
            len(lanes), [l["lane_id"] for l in lanes.values()
                         if l["origin_city"] == l["dest_city"] or l["distance_miles"] is None
                         or l["distance_miles"] <= 0])

    # ---------------- shipments
    legs = defaultdict(list)
    for l in t["shipment_leg"]:
        legs[l["shipment_id"]].append(l)
    bad_route, bad_dates, bad_contract = [], [], []
    for sid, s in ships.items():
        ls = sorted(legs[sid], key=lambda l: l["leg_seq"])
        lane = lanes.get(s["lane_id"])                  # None if the lane link is broken
        if lane is None or not ls or ls[0]["origin_city"] != lane["origin_city"] or \
                ls[-1]["dest_city"] != lane["dest_city"] or \
                any(a["dest_city"] != b["origin_city"] for a, b in zip(ls, ls[1:])) or \
                [l["leg_seq"] for l in ls] != list(range(1, len(ls) + 1)):
            bad_route.append(sid)
        d = idx.delivery.get(sid)
        if s["planned_delivery_date"] < s["ship_date"] or not ls or \
                ls[0]["depart_date"] != s["ship_date"] or \
                any(l["arrive_date"] < l["depart_date"] for l in ls) or \
                any(b["depart_date"] < a["arrive_date"] for a, b in zip(ls, ls[1:])) or \
                d is None or d["delivered_date"] != ls[-1]["arrive_date"] or \
                any(not (s["ship_date"] <= m["measured_date"] <= d["delivered_date"])
                    for m in idx.measurements[sid]):
            bad_dates.append(sid)
        card = idx.card_for(s["carrier_id"], s["ship_date"])
        if not card or not idx.tiers.get((card["rate_card_id"], s["lane_id"], s["service_level"])):
            bad_contract.append(sid)
        elif any(code not in idx.card_acc[card["rate_card_id"]] for code in idx.performed.get(sid, {})):
            bad_contract.append(f"{sid} performed an accessorial not in contract")
    rep.add("Shipment legs: start at origin, end at destination, connected", "lifecycle",
            len(ships), bad_route)
    rep.add("Shipment dates: booked <= legs <= delivery; measurements in between", "date_order",
            len(ships), bad_dates)
    rep.add("Every shipment has a rate on its lane/service in the card valid on its ship date",
            "foreign_key", len(ships), bad_contract)
    rep.add("Every shipment has a shipper BOL measurement", "required", len(ships),
            [sid for sid in ships if not any(m["measurement_source"] == "SHIPPER_BOL"
                                             for m in idx.measurements[sid])])
    rep.add("Delivery outcome = ON_TIME exactly when delivered by planned date", "derived_values",
            len(t["delivery_record"]),
            [d["delivery_id"] for d in t["delivery_record"]
             if (d["delivered_date"] <= ships[d["shipment_id"]]["planned_delivery_date"])
             != (d["delivery_outcome"] == "ON_TIME")])

    # ---------------- invoices: arithmetic
    lines_by = defaultdict(list)
    for l in t["invoice_line"]:
        lines_by[l["invoice_id"]].append(l)
    bad_total, bad_line = [], []
    for inv in t["freight_invoice"]:
        ls = lines_by[inv["invoice_id"]]
        if sum((l["amount"] for l in ls), ZERO) != inv["total_billed"]:
            bad_total.append(inv["invoice_id"])
        if sorted(l["line_no"] for l in ls) != list(range(1, len(ls) + 1)):
            bad_line.append(f"{inv['invoice_id']} line numbers not 1..n")
        for l in ls:
            if l["charge_type"] == "FUEL":
                ok = l["amount"] == money(l["quantity"] * l["unit_rate"] / Decimal(100))
            elif l["min_charge_applied"]:
                card = idx.cards[inv["rate_card_id"]]
                ok = l["amount"] == card["min_charge"] and \
                    money(l["quantity"] * l["unit_rate"]) < l["amount"]
            else:
                ok = l["amount"] == money(l["quantity"] * l["unit_rate"])
            if not ok:
                bad_line.append(l["line_id"])
            if (l["charge_type"] == "ACCESSORIAL") != (l["accessorial_code"] is not None):
                bad_line.append(f"{l['line_id']} accessorial code mismatch")
    rep.add("Invoice total = sum of its lines", "arithmetic", len(t["freight_invoice"]), bad_total)
    rep.add("Invoice line amount = quantity x rate (or minimum charge)", "arithmetic",
            len(t["invoice_line"]), bad_line)

    # ---------------- audit results
    ex_by_inv = defaultdict(list)
    for ex in t["audit_exception"]:
        ex_by_inv[ex["invoice_id"]].append(ex)
    bad_var, bad_recon, bad_repro = [], [], []
    for ex in t["audit_exception"]:
        if ex["variance_amount"] != ex["billed_amount"] - ex["expected_amount"]:
            bad_var.append(ex["exception_id"])
    for a in t["invoice_audit"]:
        exs = [e for e in ex_by_inv[a["invoice_id"]]
               if e["exception_type"] != "CREDIT_EXCEEDS_EXCEPTION"]
        if a["variance_total"] != a["billed_total"] - a["expected_total"] or \
                sum((e["variance_amount"] for e in exs), ZERO) != a["variance_total"]:
            bad_recon.append(a["invoice_id"])
        if a["shipment_id"] and a["audit_result"] != "REJECTED":
            try:
                if idx.expected(a["shipment_id"]).total != a["expected_total"]:
                    bad_repro.append(a["invoice_id"])
            except (KeyError, TypeError, ValueError) as exc:   # e.g. no rate card covers the ship date
                bad_repro.append(f"{a['invoice_id']} cannot be re-priced ({type(exc).__name__})")
    rep.add("Exception variance = billed - expected", "arithmetic", len(t["audit_exception"]),
            bad_var)
    rep.add("Exceptions explain the whole invoice difference (billed - expected = sum of "
            "exception variances)", "arithmetic", len(t["invoice_audit"]), bad_recon)
    rep.add("Expected charges are reproducible (recomputed from source records)",
            "derived_values", len(t["invoice_audit"]), bad_repro)
    rep.add("Every invoice was audited exactly once", "required", len(t["freight_invoice"]),
            sorted({i["invoice_id"] for i in t["freight_invoice"]}
                   ^ {a["invoice_id"] for a in t["invoice_audit"]}))
    # duplicates & orphans are all caught
    accepted = Counter(a["shipment_id"] for a in t["invoice_audit"]
                       if a["audit_result"] != "REJECTED")
    rep.add("Duplicate bills: at most one accepted invoice per shipment", "duplicates",
            len(accepted), [f"{s} x{n}" for s, n in accepted.items() if n > 1])
    types = {i: {e["exception_type"] for e in ex_by_inv[i]} for i in ex_by_inv}
    rep.add("Invoices quoting an unknown shipment are all flagged UNKNOWN_SHIPMENT",
            "foreign_key", len(t["freight_invoice"]),
            [i["invoice_id"] for i in t["freight_invoice"] if i["shipment_ref"] not in ships
             and "UNKNOWN_SHIPMENT" not in types.get(i["invoice_id"], set())])

    # ---------------- credits reconcile to approved disputes
    ever = defaultdict(set)
    for e in t["exception_status_history"]:
        ever[e["exception_id"]].add(e["to_status"])
    applied = defaultdict(lambda: ZERO)
    for cn in t["credit_note"]:
        if cn["current_status"] == "APPLIED":
            applied[cn["exception_id"]] += cn["credit_amount"]
    bad_credit, bad_appr = [], []
    exs = {e["exception_id"]: e for e in t["audit_exception"]}
    for eid, amt in applied.items():
        if amt > exs[eid]["approved_amount"]:
            bad_credit.append(f"{eid} credited {amt} > approved {exs[eid]['approved_amount']}")
    held_flagged = {e["credit_note_id"] for e in t["audit_exception"] if e["credit_note_id"]}
    for cn in t["credit_note"]:
        if cn["current_status"] == "HELD" and cn["credit_note_id"] not in held_flagged:
            bad_credit.append(f"{cn['credit_note_id']} held without an exception")
        if "DISPUTED" not in ever[cn["exception_id"]]:
            bad_credit.append(f"{cn['credit_note_id']} credits an undisputed exception")
    for e in t["audit_exception"]:
        expected = e["variance_amount"] if "DISPUTED" in ever[e["exception_id"]] else ZERO
        if e["approved_amount"] != expected:
            bad_appr.append(e["exception_id"])
        if e["current_status"] == "CREDITED" and applied[e["exception_id"]] != e["approved_amount"]:
            bad_appr.append(f"{e['exception_id']} CREDITED but not fully credited")
    rep.add("Credits reconcile: applied credits never exceed the approved amount", "arithmetic",
            len(t["credit_note"]), bad_credit)
    rep.add("Approved amount = variance for disputed exceptions, else 0", "derived_values",
            len(t["audit_exception"]), bad_appr)
    dispute_date = {}
    for e in t["exception_status_history"]:
        if e["to_status"] == "DISPUTED":
            dispute_date.setdefault(e["exception_id"], e["event_date"])
    inv_dates = {i["invoice_id"]: i["invoice_date"] for i in t["freight_invoice"]}
    bad = [cn["credit_note_id"] for cn in t["credit_note"]
           if cn["issued_date"] <= dispute_date.get(cn["exception_id"], cn["issued_date"])]
    bad += [a["invoice_id"] for a in t["invoice_audit"] if a["audit_date"] < inv_dates[a["invoice_id"]]]
    bad += [i["invoice_id"] for i in t["freight_invoice"] if i["shipment_ref"] in idx.delivery
            and i["invoice_date"] < idx.delivery[i["shipment_ref"]]["delivered_date"]]
    rep.add("Dates: delivery <= invoice <= audit < credit note", "date_order",
            len(t["freight_invoice"]) + len(t["credit_note"]), bad)

    # ---------------- lifecycles
    for entity, table, hist, key in [
            ("shipment", "shipment", "shipment_status_history", "shipment_id"),
            ("invoice", "freight_invoice", "invoice_status_history", "invoice_id"),
            ("exception", "audit_exception", "exception_status_history", "exception_id"),
            ("credit_note", "credit_note", "credit_note_status_history", "credit_note_id")]:
        allowed = TRANSITIONS[entity]
        events = defaultdict(list)
        for e in t[hist]:
            events[e[key]].append(e)
        bad = []
        for rec in t[table]:
            evs = sorted(events[rec[key]], key=lambda e: e["event_id"])
            prev, prev_date = None, None
            for e in evs:
                if e["from_status"] != prev or e["to_status"] not in allowed.get(prev, set()):
                    bad.append(f"{rec[key]}: {prev} -> {e['to_status']}")
                    break
                if prev_date and e["event_date"] < prev_date:
                    bad.append(f"{rec[key]}: event dates go backwards")
                    break
                prev, prev_date = e["to_status"], e["event_date"]
            if prev != rec["current_status"]:
                bad.append(f"{rec[key]}: current_status {rec['current_status']} != last event {prev}")
        rep.add(f"{entity}: status changes follow the allowed lifecycle, history matches current "
                f"status", "lifecycle", len(t[table]), bad)
        if events and set(events) - {r[key] for r in t[table]}:
            rep.add(f"{entity}: history rows point to real records", "foreign_key", len(t[hist]),
                    sorted(set(events) - {r[key] for r in t[table]}))

    # ---------------- no leakage of the answer key
    leak_words = ("ground", "truth", "label", "anomaly", "issue_type", "injected", "is_error")
    bad = [f"{tb.name}.{c.name}" for tb in TABLES for c in tb.cols
           if any(w in c.name for w in leak_words)]
    rep.add("No answer-key columns inside the business data (no leakage)", "ground_truth",
            sum(len(tb.cols) for tb in TABLES), bad)
    targets = {"INVOICE": {i["invoice_id"] for i in t["freight_invoice"]},
               "CREDIT_NOTE": {c["credit_note_id"] for c in t["credit_note"]}}
    rep.add("Ground truth rows point to real invoices / credit notes", "ground_truth", len(gt),
            [g["gt_id"] for g in gt if g["entity_id"] not in targets[g["entity_type"]]])


def validate(t, gt, cfg):
    rep = Report()
    schema_checks(rep, t, gt)
    try:
        business_checks(rep, t, gt, cfg)
    except Exception as exc:          # badly broken data must be reported, never crash the checker
        rep.add("Business checks ran to completion (data broken too badly to finish)", "integrity",
                1, [f"{type(exc).__name__}: {exc}"])
    return rep
