"""Unit tests for freight_audit/audit.py (the detective).

Each test takes one CORRECT bill from a freshly generated world, breaks one thing
on purpose, runs the auditor and checks that exactly the right exception appears.
"""
import unittest
from decimal import Decimal as D

from freight_audit.audit import Auditor
from freight_audit.pricing import ZERO, fuel_charge, money
from tests.helpers import clean_invoice, exceptions_for, generated_world, next_day, run_audit, types_for

DAY_LINE = "FLINE-{}-{:02d}"


def add_line(t, inv, **fields):
    n = max(l["line_no"] for l in t["invoice_line"] if l["invoice_id"] == inv["invoice_id"]) + 1
    line = dict(line_id=DAY_LINE.format(inv["invoice_id"], n), invoice_id=inv["invoice_id"], line_no=n,
                **{"accessorial_code": None, "min_charge_applied": 0, **fields})
    t["invoice_line"].append(line)
    return line


class AuditCase(unittest.TestCase):
    def setUp(self):
        self.cfg, self.t, self.gt, _ = generated_world()
        self.used = set()

    def pick(self, where=None):
        """A clean bill not already used by this test."""
        c = clean_invoice(self.t, self.gt, self.cfg,
                          lambda c: c["inv"]["invoice_id"] not in self.used and (where is None or where(c)))
        self.used.add(c["inv"]["invoice_id"])
        return c


class TestInvoiceLevelChecks(AuditCase):
    def test_correct_bill_is_clean(self):
        c = self.pick()
        run_audit(self.t, self.cfg)
        audit = next(a for a in self.t["invoice_audit"] if a["invoice_id"] == c["inv"]["invoice_id"])
        self.assertEqual(audit["audit_result"], "CLEAN")
        self.assertEqual(audit["variance_total"], ZERO)
        self.assertEqual(exceptions_for(self.t, c["inv"]["invoice_id"]), [])

    def test_unknown_shipment_rejects_whole_bill(self):
        c = self.pick()
        c["inv"]["shipment_ref"] = "SHP-2026-999999"
        run_audit(self.t, self.cfg)
        ex = exceptions_for(self.t, c["inv"]["invoice_id"])
        self.assertEqual([e["exception_type"] for e in ex], ["UNKNOWN_SHIPMENT"])
        self.assertEqual(ex[0]["expected_amount"], ZERO)
        self.assertEqual(ex[0]["variance_amount"], c["inv"]["total_billed"])
        self.assertIsNone(ex[0]["line_id"])

    def test_wrong_carrier_rejects_whole_bill(self):
        c = self.pick()
        other = next(x["carrier_id"] for x in self.t["carrier"] if x["carrier_id"] != c["inv"]["carrier_id"])
        c["inv"]["carrier_id"] = other
        run_audit(self.t, self.cfg)
        self.assertEqual(types_for(self.t, c["inv"]["invoice_id"]), ["CARRIER_MISMATCH"])

    def test_second_bill_for_same_shipment_is_duplicate(self):
        c = self.pick()
        copy_inv = dict(c["inv"], invoice_id="FINV-2026-999999", invoice_date=next_day(c["inv"]["invoice_date"], 30))
        self.t["freight_invoice"].append(copy_inv)
        for l in c["lines"]:
            self.t["invoice_line"].append(dict(l, invoice_id=copy_inv["invoice_id"], line_id=DAY_LINE.format(copy_inv["invoice_id"], l["line_no"])))
        run_audit(self.t, self.cfg)
        self.assertEqual(types_for(self.t, c["inv"]["invoice_id"]), [])                 # first bill stays clean
        dup = exceptions_for(self.t, copy_inv["invoice_id"])
        self.assertEqual([e["exception_type"] for e in dup], ["DUPLICATE_INVOICE"])
        self.assertIn(c["inv"]["invoice_id"], dup[0]["detail"])
        self.assertIn("identical", dup[0]["detail"])


class TestLineChecks(AuditCase):
    def test_wrong_weight_band_with_fuel_knock_on_linked_to_root(self):
        c = self.pick(lambda c: c["exp"].tier_index > 0 and c["exp"].base > c["exp"].card["min_charge"] * 2)
        e, base, fuel = c["exp"], c["lines"][0], c["lines"][1]
        wrong = e.tiers[e.tier_index - 1]["rate_per_lb"]
        base.update(unit_rate=wrong, amount=money(e.billable_lb * wrong))
        fuel.update(quantity=base["amount"], amount=fuel_charge(base["amount"], e.fuel_pct))
        run_audit(self.t, self.cfg)
        ex = {x["exception_type"]: x for x in exceptions_for(self.t, c["inv"]["invoice_id"])}
        self.assertEqual(set(ex), {"WEIGHT_TIER_MISAPPLIED", "FUEL_ON_INCORRECT_BASE"})
        self.assertEqual(ex["FUEL_ON_INCORRECT_BASE"]["root_exception_id"], ex["WEIGHT_TIER_MISAPPLIED"]["exception_id"])
        self.assertEqual(ex["WEIGHT_TIER_MISAPPLIED"]["line_id"], base["line_id"])

    def test_rate_that_is_on_no_tier(self):
        c = self.pick(lambda c: c["exp"].base > c["exp"].card["min_charge"] * 2)
        c["lines"][0].update(unit_rate=c["exp"].rate + D("0.0001"))
        c["lines"][0]["amount"] = money(c["exp"].billable_lb * c["lines"][0]["unit_rate"])
        run_audit(self.t, self.cfg)
        self.assertIn("BASE_RATE_MISMATCH", types_for(self.t, c["inv"]["invoice_id"]))

    def test_inflated_weight(self):
        c = self.pick(lambda c: c["exp"].base > c["exp"].card["min_charge"] * 2)
        c["lines"][0].update(quantity=D(c["exp"].billable_lb + 400))
        c["lines"][0]["amount"] += D("40.00")
        run_audit(self.t, self.cfg)
        self.assertIn("DIM_WEIGHT_DISCREPANCY", types_for(self.t, c["inv"]["invoice_id"]))

    def test_expired_rate_card_is_flagged_even_without_dollar_impact(self):
        c = self.pick(lambda c: c["ship"]["ship_date"] >= self.cfg.h2_start)
        c["inv"]["rate_card_id"] = c["exp"].card["previous_rate_card_id"]
        run_audit(self.t, self.cfg)
        self.assertEqual(types_for(self.t, c["inv"]["invoice_id"]), ["RATE_CARD_EXPIRED"])

    def test_wrong_fuel_percent(self):
        c = self.pick(lambda c: c["exp"].base > 100)
        fuel = c["lines"][1]
        fuel.update(unit_rate=c["exp"].fuel_pct + 3, amount=fuel_charge(c["exp"].base, c["exp"].fuel_pct + 3))
        run_audit(self.t, self.cfg)
        ex = exceptions_for(self.t, c["inv"]["invoice_id"])
        self.assertEqual([x["exception_type"] for x in ex], ["FUEL_SURCHARGE_MISCALCULATED"])
        self.assertEqual(ex[0]["expected_amount"], c["exp"].fuel)

    def test_duplicated_fuel_line(self):
        c = self.pick()
        fuel = c["lines"][1]
        extra = add_line(self.t, c["inv"], charge_type="FUEL", quantity=fuel["quantity"], unit="PCT_OF_BASE",
                         unit_rate=fuel["unit_rate"], amount=fuel["amount"])
        run_audit(self.t, self.cfg)
        ex = exceptions_for(self.t, c["inv"]["invoice_id"])
        self.assertEqual([x["exception_type"] for x in ex], ["SURCHARGE_DUPLICATED"])
        self.assertEqual(ex[0]["line_id"], extra["line_id"])
        self.assertEqual(ex[0]["expected_amount"], ZERO)

    def test_service_not_on_delivery_record(self):
        def ok(c):
            contract = c["idx"].card_acc[c["exp"].card["rate_card_id"]]
            done = c["idx"].performed.get(c["ship"]["shipment_id"], {})
            return any(code not in done for code in contract)
        c = self.pick(ok)
        contract = c["idx"].card_acc[c["exp"].card["rate_card_id"]]
        done = c["idx"].performed.get(c["ship"]["shipment_id"], {})
        code = next(x for x in contract if x not in done)
        add_line(self.t, c["inv"], charge_type="ACCESSORIAL", accessorial_code=code, quantity=D("1"), unit="EACH",
                 unit_rate=contract[code], amount=contract[code])
        run_audit(self.t, self.cfg)
        ex = exceptions_for(self.t, c["inv"]["invoice_id"])
        self.assertEqual([x["exception_type"] for x in ex], ["INVALID_ACCESSORIAL"])
        self.assertIn("not recorded on delivery", ex[0]["detail"])

    def test_service_price_differs_from_contract(self):
        c = self.pick(lambda c: c["exp"].accessorials)
        acc = next(l for l in c["lines"] if l["charge_type"] == "ACCESSORIAL")
        acc["amount"] += D("15.00")
        run_audit(self.t, self.cfg)
        self.assertEqual(types_for(self.t, c["inv"]["invoice_id"]), ["ACCESSORIAL_AMOUNT_MISMATCH"])

    def test_performed_service_missing_from_bill_is_unbilled(self):
        c = self.pick(lambda c: c["exp"].accessorials)
        acc = next(l for l in c["lines"] if l["charge_type"] == "ACCESSORIAL")
        self.t["invoice_line"].remove(acc)
        run_audit(self.t, self.cfg)
        ex = exceptions_for(self.t, c["inv"]["invoice_id"])
        self.assertEqual([x["exception_type"] for x in ex], ["UNBILLED_CHARGE"])
        self.assertLess(ex[0]["variance_amount"], ZERO)

    def test_every_dollar_is_explained(self):
        """billed - expected == sum of exception variances, for every non-rejected bill (planted ones included)."""
        run_audit(self.t, self.cfg)
        for a in self.t["invoice_audit"]:
            if a["audit_result"] == "REJECTED":
                continue
            total = sum((x["variance_amount"] for x in exceptions_for(self.t, a["invoice_id"])), ZERO)
            self.assertEqual(total, a["billed_total"] - a["expected_total"], a["invoice_id"])


class TestIds(unittest.TestCase):
    def test_exception_ids_are_sequential_and_start_open(self):
        cfg, t, gt, _ = generated_world()
        run_audit(t, cfg)
        ids = [e["exception_id"] for e in t["audit_exception"]]
        self.assertEqual(ids, [f"FEX-2026-{i:06d}" for i in range(1, len(ids) + 1)])
        self.assertTrue(all(e["current_status"] == "OPEN" for e in t["audit_exception"]))


class TestDecisionsCreditsAndClosing(AuditCase):
    def overbill(self, amount):
        """Add a duplicated-looking extra line worth `amount` to a clean bill."""
        c = self.pick()
        add_line(self.t, c["inv"], charge_type="FUEL", quantity=D("1"), unit="PCT_OF_BASE", unit_rate=D("1"), amount=amount)
        return c

    def test_dispute_threshold_is_five_dollars(self):
        small, big = self.overbill(D("4.99")), self.overbill(D("5.00"))
        run_audit(self.t, self.cfg, decide=True)
        s = exceptions_for(self.t, small["inv"]["invoice_id"])[0]
        b = exceptions_for(self.t, big["inv"]["invoice_id"])[0]
        self.assertEqual((s["current_status"], s["approved_amount"]), ("WAIVED", ZERO))
        self.assertEqual((b["current_status"], b["approved_amount"]), ("DISPUTED", D("5.00")))
        self.assertEqual(small["inv"]["current_status"], "PAID")         # waived-only bills are approved and paid
        self.assertEqual(big["inv"]["current_status"], "DISPUTED")

    def test_underbilling_is_acknowledged(self):
        c = self.pick(lambda c: c["exp"].accessorials)
        self.t["invoice_line"].remove(next(l for l in c["lines"] if l["charge_type"] == "ACCESSORIAL"))
        run_audit(self.t, self.cfg, decide=True)
        self.assertEqual(exceptions_for(self.t, c["inv"]["invoice_id"])[0]["current_status"], "ACKNOWLEDGED")

    def test_clean_bill_paid_after_payment_terms(self):
        c = self.pick()
        run_audit(self.t, self.cfg, decide=True)
        paid = next(h for h in self.t["invoice_status_history"] if h["invoice_id"] == c["inv"]["invoice_id"] and h["to_status"] == "PAID")
        self.assertEqual(paid["event_date"], next_day(c["inv"]["invoice_date"], 1 + self.cfg.payment_terms_days))

    def test_rejected_bill_and_its_exception(self):
        c = self.pick()
        c["inv"]["shipment_ref"] = "SHP-2026-999999"
        run_audit(self.t, self.cfg, decide=True)
        self.assertEqual(c["inv"]["current_status"], "REJECTED")
        self.assertEqual(exceptions_for(self.t, c["inv"]["invoice_id"])[0]["current_status"], "INVOICE_REJECTED")

    def credit(self, ex, amount, n=1):
        cn = dict(credit_note_id=f"CN-2026-9{n:05d}", exception_id=ex["exception_id"], invoice_id=ex["invoice_id"],
                  carrier_id="x", issued_date=next_day(ex["detected_date"], 10 + n), credit_amount=amount, current_status="ISSUED")
        self.t["credit_note"].append(cn)
        return cn

    def disputed_case(self):
        c = self.overbill(D("50.00"))
        a = run_audit(self.t, self.cfg, decide=True)
        self.t["credit_note"].clear()
        ex = exceptions_for(self.t, c["inv"]["invoice_id"])[0]
        return a, c, ex

    def test_full_credit_resolves_then_pays(self):
        a, c, ex = self.disputed_case()
        cn = self.credit(ex, D("50.00"))
        a.reconcile_credits()
        a.close_disputed_invoices()
        self.assertEqual((cn["current_status"], ex["current_status"]), ("APPLIED", "CREDITED"))
        self.assertEqual(c["inv"]["current_status"], "PAID")
        steps = [h["to_status"] for h in self.t["invoice_status_history"] if h["invoice_id"] == c["inv"]["invoice_id"]]
        self.assertEqual(steps[-2:], ["RESOLVED", "PAID"])

    def test_partial_credit_escalates(self):
        a, c, ex = self.disputed_case()
        self.credit(ex, D("20.00"))
        a.reconcile_credits()
        a.close_disputed_invoices()
        self.assertEqual(ex["current_status"], "PARTIALLY_CREDITED")
        self.assertEqual(c["inv"]["current_status"], "ESCALATED")

    def test_two_credits_adding_up_to_approved_is_credited(self):
        a, c, ex = self.disputed_case()
        self.credit(ex, D("30.00"), 1)
        self.credit(ex, D("20.00"), 2)
        a.reconcile_credits()
        self.assertEqual(ex["current_status"], "CREDITED")

    def test_over_credit_is_held_and_raises_its_own_exception(self):
        a, c, ex = self.disputed_case()
        cn = self.credit(ex, D("65.00"))
        a.reconcile_credits()
        a.close_disputed_invoices()
        self.assertEqual(cn["current_status"], "HELD")
        self.assertEqual(ex["current_status"], "CREDIT_ON_HOLD")
        over = next(x for x in self.t["audit_exception"] if x["credit_note_id"] == cn["credit_note_id"])
        self.assertEqual(over["exception_type"], "CREDIT_EXCEEDS_EXCEPTION")
        self.assertEqual(over["variance_amount"], D("15.00"))
        self.assertEqual(over["root_exception_id"], ex["exception_id"])
        self.assertEqual(over["current_status"], "DISPUTED")
        self.assertEqual(c["inv"]["current_status"], "ESCALATED")

    def test_second_credit_beyond_remaining_is_held(self):
        a, c, ex = self.disputed_case()
        first, second = self.credit(ex, D("40.00"), 1), self.credit(ex, D("20.00"), 2)
        a.reconcile_credits()
        self.assertEqual((first["current_status"], second["current_status"]), ("APPLIED", "HELD"))

    def test_auditor_never_receives_the_answer_key(self):
        import inspect
        params = inspect.signature(Auditor.__init__).parameters
        self.assertEqual(list(params), ["self", "t", "cfg"])


if __name__ == "__main__":
    unittest.main()
