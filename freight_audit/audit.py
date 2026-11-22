"""Part B - the "detective" (audit engine).

It reads ONLY the business data (invoices, shipments, rate cards, delivery
records). It never sees ground_truth. For each invoice it:
    1. checks the bill points to a real, not-yet-billed shipment of that carrier
    2. recalculates what every line SHOULD cost from the contract
    3. raises one AuditException per wrong line, with the reason and the $ difference
Then the analyst rules decide (dispute / waive / reject), and later the
credit notes the carrier sends are reconciled against what was approved.
"""
from collections import defaultdict
from datetime import timedelta

from .history import transition
from .pricing import ZERO, Index, ceil_int, fuel_charge, money

DAY = timedelta(days=1)


def _tier_text(t):
    return f"{t['tier_min_lb']}+ lb" if t["tier_max_lb"] is None else \
        f"{t['tier_min_lb']}-{t['tier_max_lb']} lb"


class Auditor:
    def __init__(self, t, cfg):
        self.t, self.cfg = t, cfg
        self.idx = Index(t)
        self.tol = cfg.tolerance
        self.exceptions = t["audit_exception"]
        self.ex_hist = t["exception_status_history"]
        self.by_id = {}

    # ------------------------------------------------------------ helpers
    def raise_exception(self, inv, line, kind, expected, billed, detail, when, shipment=None,
                        card=None, root=None, credit_note=None):
        ex = dict(exception_id=f"FEX-{self.cfg.year}-{len(self.exceptions) + 1:06d}",
                  invoice_id=inv["invoice_id"], line_id=line["line_id"] if line else None,
                  shipment_id=shipment, rate_card_id=card,
                  root_exception_id=root["exception_id"] if root else None,
                  credit_note_id=credit_note, exception_type=kind, expected_amount=expected,
                  billed_amount=billed, variance_amount=billed - expected,
                  approved_amount=ZERO, detail=detail, detected_date=when, current_status=None)
        self.exceptions.append(ex)
        self.by_id[ex["exception_id"]] = ex
        transition(ex, self.ex_hist, "exception_id", "OPEN", when, "AUDIT_ENGINE", "Raised by audit")
        return ex

    # ------------------------------------------------------------ step 1: audit
    def audit_all(self):
        t, idx, tol = self.t, self.idx, self.tol
        lines_by_inv = defaultdict(list)
        for l in t["invoice_line"]:
            lines_by_inv[l["invoice_id"]].append(l)
        inv_hist = t["invoice_status_history"]
        first_bill = {}
        results = []
        for inv in sorted(t["freight_invoice"], key=lambda i: (i["invoice_date"], i["invoice_id"])):
            inv["current_status"] = "RECEIVED"
            when = inv["invoice_date"] + DAY
            transition(inv, inv_hist, "invoice_id", "AUDITED", when, "AUDIT_ENGINE",
                       "Automatic audit against contract and delivery record")
            lines = sorted(lines_by_inv[inv["invoice_id"]], key=lambda l: l["line_no"])
            billed_total = inv["total_billed"]
            start = len(self.exceptions)
            s = idx.shipments.get(inv["shipment_ref"])
            reject = None
            if s is None:
                reject = ("UNKNOWN_SHIPMENT",
                          f"Invoice quotes shipment {inv['shipment_ref']}, which does not exist")
            elif s["carrier_id"] != inv["carrier_id"]:
                reject = ("CARRIER_MISMATCH",
                          f"Shipment {s['shipment_id']} was hauled by {s['carrier_id']}, "
                          f"not {inv['carrier_id']}")
            elif s["shipment_id"] in first_bill:
                prev = first_bill[s["shipment_id"]]
                same = "identical" if prev["total_billed"] == billed_total else "different"
                reject = ("DUPLICATE_INVOICE",
                          f"Shipment {s['shipment_id']} was already billed on {prev['invoice_id']} "
                          f"({same} amount ${prev['total_billed']})")
            if reject:
                self.raise_exception(inv, None, reject[0], ZERO, billed_total, reject[1], when,
                                     shipment=s["shipment_id"] if s else None)
                results.append(dict(invoice_id=inv["invoice_id"],
                                    shipment_id=s["shipment_id"] if s else None,
                                    expected_rate_card_id=None, expected_billable_weight_lb=None,
                                    audit_date=when, billed_total=billed_total,
                                    expected_total=ZERO, variance_total=billed_total,
                                    exception_count=1, audit_result="REJECTED"))
                continue
            first_bill[s["shipment_id"]] = inv
            e = idx.expected(s["shipment_id"])
            self._audit_lines(inv, lines, s, e, when)
            n = len(self.exceptions) - start
            results.append(dict(invoice_id=inv["invoice_id"], shipment_id=s["shipment_id"],
                                expected_rate_card_id=e.card["rate_card_id"],
                                expected_billable_weight_lb=e.billable_lb, audit_date=when,
                                billed_total=billed_total, expected_total=e.total,
                                variance_total=billed_total - e.total, exception_count=n,
                                audit_result="EXCEPTIONS" if n else "CLEAN"))
        t["invoice_audit"] = sorted(results, key=lambda r: r["invoice_id"])

    def _audit_lines(self, inv, lines, s, e, when):
        idx, tol = self.idx, self.tol
        sid, card_id = s["shipment_id"], e.card["rate_card_id"]
        billed_card = idx.cards[inv["rate_card_id"]]
        raise_ = lambda line, kind, exp, billed, detail, root=None: self.raise_exception(
            inv, line, kind, exp, billed, detail, when, shipment=sid, card=card_id, root=root)
        base_lines = [l for l in lines if l["charge_type"] == "BASE"]
        fuel_lines = [l for l in lines if l["charge_type"] == "FUEL"]
        acc_lines = [l for l in lines if l["charge_type"] == "ACCESSORIAL"]

        # ---- base freight
        root = None
        if base_lines:
            bl = base_lines[0]
            kind, detail = None, ""
            if inv["rate_card_id"] != card_id:
                if billed_card["carrier_id"] == inv["carrier_id"] and \
                        not idx.is_effective(billed_card, s["ship_date"]):
                    kind = "RATE_CARD_EXPIRED"
                    detail = (f"Billed with {billed_card['rate_card_id']} valid "
                              f"{billed_card['effective_from']} to {billed_card['effective_to']}; "
                              f"ship date {s['ship_date']} requires {card_id}")
                else:
                    kind = "RATE_CARD_MISMATCH"
                    detail = f"Billed with {billed_card['rate_card_id']}; contract is {card_id}"
            elif ceil_int(bl["quantity"]) != e.billable_lb:
                kind = "DIM_WEIGHT_DISCREPANCY"
                detail = (f"Billed {ceil_int(bl['quantity'])} lb; measurements give actual "
                          f"{e.actual_lb} lb, DIM {e.dim_lb} lb (factor {e.card['dim_factor']}), "
                          f"so billable is {e.billable_lb} lb")
            elif bl["unit_rate"] != e.rate:
                other = [x for x in e.tiers if x["rate_per_lb"] == bl["unit_rate"]]
                if other:
                    kind = "WEIGHT_TIER_MISAPPLIED"
                    detail = (f"{e.billable_lb} lb belongs in tier {_tier_text(e.tiers[e.tier_index])}"
                              f" at {e.rate}/lb, but the {_tier_text(other[0])} rate "
                              f"{bl['unit_rate']}/lb was used")
                else:
                    kind = "BASE_RATE_MISMATCH"
                    detail = f"Rate {bl['unit_rate']}/lb is not on the rate card ({e.rate}/lb)"
            elif abs(bl["amount"] - e.base) > tol:
                kind = "BASE_AMOUNT_MISMATCH"
                detail = f"Base should be ${e.base}"
            variance = bl["amount"] - e.base
            if kind in ("RATE_CARD_EXPIRED", "RATE_CARD_MISMATCH") or (kind and abs(variance) > tol):
                root = raise_(bl, kind, e.base, bl["amount"], detail)
            for extra in base_lines[1:]:
                raise_(extra, "SURCHARGE_DUPLICATED", ZERO, extra["amount"],
                       f"Base freight billed again (duplicate of line {bl['line_no']})")
        else:
            raise_(None, "UNBILLED_CHARGE", e.base, ZERO, "No base freight line was billed")

        # ---- fuel surcharge
        if fuel_lines:
            fl = fuel_lines[0]
            if abs(fl["amount"] - e.fuel) > tol:
                billed_base = base_lines[0]["amount"] if base_lines else ZERO
                if root and fl["amount"] == fuel_charge(billed_base, billed_card["fuel_surcharge_pct"]):
                    raise_(fl, "FUEL_ON_INCORRECT_BASE", e.fuel, fl["amount"],
                           f"Fuel {fl['unit_rate']}% applied to the incorrect base ${billed_base}; "
                           f"correct is {e.fuel_pct}% of ${e.base} = ${e.fuel} "
                           f"(knock-on of {root['exception_id']})", root=root)
                else:
                    raise_(fl, "FUEL_SURCHARGE_MISCALCULATED", e.fuel, fl["amount"],
                           f"Fuel billed at {fl['unit_rate']}% = ${fl['amount']}; contract is "
                           f"{e.fuel_pct}% of ${e.base} = ${e.fuel}")
            for extra in fuel_lines[1:]:
                raise_(extra, "SURCHARGE_DUPLICATED", ZERO, extra["amount"],
                       f"Fuel surcharge billed again (duplicate of line {fl['line_no']})")
        else:
            raise_(None, "UNBILLED_CHARGE", e.fuel, ZERO, "No fuel surcharge line was billed")

        # ---- accessorials
        contract = idx.card_acc[card_id]
        performed = idx.performed.get(sid, {})
        dlv = idx.delivery[sid]["delivery_id"]
        groups = {}
        for l in acc_lines:
            groups.setdefault(l["accessorial_code"], []).append(l)
        for code, ls in groups.items():
            if code not in contract:
                for l in ls:
                    raise_(l, "INVALID_ACCESSORIAL", ZERO, l["amount"],
                           f"{code} is not allowed by rate card {card_id}")
            elif code not in performed:
                for l in ls:
                    raise_(l, "INVALID_ACCESSORIAL", ZERO, l["amount"],
                           f"{code} billed but not recorded on delivery {dlv}")
            else:
                expected = money(contract[code] * performed[code])
                first = ls[0]
                if abs(first["amount"] - expected) > tol:
                    raise_(first, "ACCESSORIAL_AMOUNT_MISMATCH", expected, first["amount"],
                           f"{code}: contract ${contract[code]} x {performed[code]} = ${expected}")
                for extra in ls[1:]:
                    raise_(extra, "SURCHARGE_DUPLICATED", ZERO, extra["amount"],
                           f"{code} billed again (duplicate of line {first['line_no']})")
        for code, qty, rate, amount, unit in e.accessorials:
            if code not in groups:
                raise_(None, "UNBILLED_CHARGE", amount, ZERO,
                       f"{code} was performed (delivery {dlv}) but not billed")

    # ------------------------------------------------------------ step 2: analyst rules
    def decide(self):
        """Documented decision rules (no randomness):
           * invoice-level problem (unknown shipment, duplicate, wrong carrier) -> reject invoice
           * overbilled by >= $5 -> dispute;  overbilled < $5 or $0 impact -> waive
           * underbilled (carrier charged too little) -> acknowledge
           * no exceptions -> approve, then pay on 30-day terms"""
        cfg, t = self.cfg, self.t
        inv_by_id = {i["invoice_id"]: i for i in t["freight_invoice"]}
        ex_by_inv = defaultdict(list)
        for ex in self.exceptions:
            ex_by_inv[ex["invoice_id"]].append(ex)
        hist = t["invoice_status_history"]
        for a in t["invoice_audit"]:
            inv, exs = inv_by_id[a["invoice_id"]], ex_by_inv[a["invoice_id"]]
            when = a["audit_date"]
            if a["audit_result"] == "REJECTED":
                transition(inv, hist, "invoice_id", "REJECTED", when, "AUDIT_ENGINE",
                           exs[0]["exception_type"])
                for ex in exs:
                    transition(ex, self.ex_hist, "exception_id", "INVOICE_REJECTED", when,
                               "AUDIT_ENGINE", "Whole invoice rejected")
                continue
            decided = when + DAY
            disputed = ZERO
            for ex in exs:
                v = ex["variance_amount"]
                if v >= cfg.waive_below:
                    ex["approved_amount"] = v
                    disputed += v
                    transition(ex, self.ex_hist, "exception_id", "DISPUTED", decided,
                               "LOGISTICS_ANALYST", f"Dispute raised with carrier for ${v}")
                elif v < -cfg.tolerance:
                    transition(ex, self.ex_hist, "exception_id", "ACKNOWLEDGED", decided,
                               "LOGISTICS_ANALYST", "Underbilled; carrier may rebill")
                else:
                    transition(ex, self.ex_hist, "exception_id", "WAIVED", decided,
                               "LOGISTICS_ANALYST", f"Below ${cfg.waive_below} dispute threshold")
            if disputed > 0:
                transition(inv, hist, "invoice_id", "DISPUTED", decided, "LOGISTICS_ANALYST",
                           f"Short-paid pending credit of ${disputed}")
            else:
                transition(inv, hist, "invoice_id", "APPROVED", decided if exs else when,
                           "LOGISTICS_ANALYST" if exs else "AUDIT_ENGINE",
                           "Approved with waived differences" if exs else "Matches contract")
                transition(inv, hist, "invoice_id", "PAID", when + timedelta(days=cfg.payment_terms_days),
                           "OPERATIONS_MANAGER", f"Paid ${inv['total_billed']}")

    # ------------------------------------------------------------ step 3: reconcile credits
    def reconcile_credits(self):
        """A credit may never exceed the approved exception amount. Over-credits are
        HELD and raise their own CREDIT_EXCEEDS_EXCEPTION exception."""
        t = self.t
        inv_by_id = {i["invoice_id"]: i for i in t["freight_invoice"]}
        cn_hist = t["credit_note_status_history"]
        applied = defaultdict(lambda: ZERO)
        for cn in sorted(t["credit_note"], key=lambda c: (c["issued_date"], c["credit_note_id"])):
            ex = self.by_id[cn["exception_id"]]
            when = cn["issued_date"] + DAY
            remaining = ex["approved_amount"] - applied[ex["exception_id"]]
            if cn["credit_amount"] > remaining + self.tol:
                transition(cn, cn_hist, "credit_note_id", "HELD", when, "AUDIT_ENGINE",
                           f"Credit ${cn['credit_amount']} exceeds remaining approved ${remaining}")
                over = self.raise_exception(
                    inv_by_id[cn["invoice_id"]], None, "CREDIT_EXCEEDS_EXCEPTION", remaining,
                    cn["credit_amount"],
                    f"Credit note {cn['credit_note_id']} for ${cn['credit_amount']} exceeds the "
                    f"${remaining} approved on {ex['exception_id']}", when,
                    shipment=ex["shipment_id"], card=ex["rate_card_id"], root=ex,
                    credit_note=cn["credit_note_id"])
                over["line_id"] = ex["line_id"]
                over["approved_amount"] = over["variance_amount"]
                transition(over, self.ex_hist, "exception_id", "DISPUTED", when,
                           "LOGISTICS_ANALYST", "Carrier asked to reissue credit for approved amount")
                transition(ex, self.ex_hist, "exception_id", "CREDIT_ON_HOLD", when,
                           "LOGISTICS_ANALYST", f"Credit {cn['credit_note_id']} held")
            else:
                applied[ex["exception_id"]] += cn["credit_amount"]
                transition(cn, cn_hist, "credit_note_id", "APPLIED", when, "AUDIT_ENGINE",
                           "Within approved amount")
                full = applied[ex["exception_id"]] == ex["approved_amount"]
                transition(ex, self.ex_hist, "exception_id",
                           "CREDITED" if full else "PARTIALLY_CREDITED", when, "AUDIT_ENGINE",
                           f"${applied[ex['exception_id']]} of ${ex['approved_amount']} credited")

    # ------------------------------------------------------------ step 4: close invoices
    def close_disputed_invoices(self):
        t = self.t
        last_date = {}
        for e in self.ex_hist:
            last_date[e["exception_id"]] = max(last_date.get(e["exception_id"], e["event_date"]),
                                               e["event_date"])
        ex_by_inv = defaultdict(list)
        for ex in self.exceptions:
            ex_by_inv[ex["invoice_id"]].append(ex)
        hist = t["invoice_status_history"]
        for inv in t["freight_invoice"]:
            if inv["current_status"] != "DISPUTED":
                continue
            disputed = [ex for ex in ex_by_inv[inv["invoice_id"]] if ex["approved_amount"] > 0
                        and ex["exception_type"] != "CREDIT_EXCEEDS_EXCEPTION"]
            when = max(last_date[ex["exception_id"]] for ex in disputed)
            if all(ex["current_status"] == "CREDITED" for ex in disputed):
                credited = sum((ex["approved_amount"] for ex in disputed), ZERO)
                transition(inv, hist, "invoice_id", "RESOLVED", when, "LOGISTICS_ANALYST",
                           f"All disputes credited (${credited})")
                transition(inv, hist, "invoice_id", "PAID", when + timedelta(days=5),
                           "OPERATIONS_MANAGER", f"Paid ${inv['total_billed'] - credited} net of credits")
            else:
                open_states = sorted({ex["current_status"] for ex in disputed} - {"CREDITED"})
                transition(inv, hist, "invoice_id", "ESCALATED", when, "LOGISTICS_ANALYST",
                           "Unresolved disputes: " + ", ".join(open_states))
