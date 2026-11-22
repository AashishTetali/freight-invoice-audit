"""Unit tests for freight_audit/validate.py: every rule passes on good data and fails on broken data."""
import unittest
from datetime import timedelta
from decimal import Decimal as D

from freight_audit.validate import Report, _key, validate
from tests.helpers import built_world


def failed(t, gt, cfg):
    return {c["name"]: c for c in validate(t, gt, cfg).summary()["checks"] if c["status"] == "FAIL"}


class TestReport(unittest.TestCase):
    def test_pass_fail_counts_and_five_examples_max(self):
        r = Report()
        r.add("good", "x", 10, [])
        r.add("bad", "x", 10, [f"e{i}" for i in range(8)])
        s = r.summary()
        self.assertEqual((s["overall"], s["checks_run"], s["checks_passed"], s["checks_failed"]), ("FAIL", 2, 1, 1))
        bad = s["checks"][1]
        self.assertEqual((bad["status"], bad["failed"], len(bad["examples"])), ("FAIL", 8, 5))

    def test_all_pass_is_pass(self):
        r = Report()
        r.add("good", "x", 1, [])
        self.assertEqual(r.summary()["overall"], "PASS")

    def test_key(self):
        self.assertEqual(_key({"a": 1, "b": 2, "c": 3}, ("a", "c")), (1, 3))


class TestValidate(unittest.TestCase):
    def setUp(self):
        self.cfg, self.t, self.gt = built_world()

    def assertFails(self, name_part):
        f = failed(self.t, self.gt, self.cfg)
        hits = [n for n in f if name_part in n]
        self.assertTrue(hits, f"expected a failing check containing {name_part!r}; failing: {list(f)}")
        return f[hits[0]]

    def test_good_world_passes_all_168_python_checks(self):
        s = validate(self.t, self.gt, self.cfg).summary()
        self.assertEqual(s["checks_run"], 168)
        self.assertEqual(s["overall"], "PASS", [c["name"] for c in s["checks"] if c["status"] == "FAIL"])

    def test_duplicate_primary_key(self):
        self.t["carrier"].append(dict(self.t["carrier"][0]))
        self.assertFails("carrier: primary key")

    def test_broken_foreign_key(self):
        self.t["shipment"][0]["carrier_id"] = "CAR-999999"
        self.assertIn("CAR-999999", " ".join(self.assertFails("shipment.carrier_id -> carrier.carrier_id")["examples"]))

    def test_value_not_allowed(self):
        self.t["shipment"][0]["service_level"] = "TELEPORT"
        self.assertFails("shipment: values in allowed lists")

    def test_bad_id_format(self):
        self.t["lane"][0]["lane_id"] = "LANE-1"
        self.assertFails("lane: ID / code formats")

    def test_missing_required_value(self):
        self.t["lane"][0]["distance_miles"] = None
        self.assertFails("lane: required fields")

    def test_line_amount_and_invoice_total(self):
        line = next(l for l in self.t["invoice_line"] if l["charge_type"] == "BASE")
        line["amount"] += D("10.00")
        self.assertFails("Invoice total = sum of its lines")
        self.assertFails("Invoice line amount")

    def test_bill_dated_before_delivery(self):
        inv = next(i for i in self.t["freight_invoice"] if i["current_status"] == "PAID")
        inv["invoice_date"] -= timedelta(days=60)
        self.assertFails("Dates: delivery <= invoice")

    def test_exception_amounts_not_explaining_the_bill(self):
        ex = next(e for e in self.t["audit_exception"] if e["exception_type"] == "SURCHARGE_DUPLICATED")
        ex["variance_amount"] += D("1.00")
        self.assertFails("Exception variance = billed - expected")
        self.assertFails("Exceptions explain the whole invoice difference")

    def test_credit_above_approved_amount(self):
        cn = next(c for c in self.t["credit_note"] if c["current_status"] == "APPLIED")
        cn["credit_amount"] += D("1000.00")
        self.assertFails("Credits reconcile")

    def test_illegal_status_change(self):
        ev = next(e for e in self.t["invoice_status_history"] if e["to_status"] == "AUDITED")
        ev["to_status"] = "PAID"
        self.assertFails("invoice: status changes follow the allowed lifecycle")

    def test_current_status_out_of_sync_with_history(self):
        self.t["freight_invoice"][0]["current_status"] = "RECEIVED"
        self.assertFails("invoice: status changes follow the allowed lifecycle")

    def test_second_accepted_bill_for_a_shipment(self):
        ok = [a for a in self.t["invoice_audit"] if a["audit_result"] != "REJECTED"]
        ok[1]["shipment_id"] = ok[0]["shipment_id"]
        self.assertFails("Duplicate bills")

    def test_answer_key_pointing_nowhere(self):
        self.gt[0]["entity_id"] = "FINV-2026-999999"
        self.assertFails("Ground truth rows point to real")

    def test_never_crashes_on_badly_broken_data(self):
        self.t["rate_card"].clear()                      # every contract gone
        s = validate(self.t, self.gt, self.cfg).summary()   # must report, not raise
        self.assertEqual(s["overall"], "FAIL")
        self.assertTrue(any("rate_card" in c["name"] for c in s["checks"] if c["status"] == "FAIL"))

    def test_rate_card_gap(self):
        card = next(c for c in self.t["rate_card"] if c["version"] == "2026-02")
        card["effective_from"] += timedelta(days=3)
        self.assertFails("Rate cards: effective dates")


if __name__ == "__main__":
    unittest.main()
