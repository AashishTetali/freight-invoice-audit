"""Integration tests: the whole system, the way a user runs it.

* the real starter run, with its known results (a regression guard)
* the command line as a separate process
* traceability from answer key -> exception -> line -> shipment -> delivery -> credit
* negative scenarios: broken data, a lenient detective, a wrong contract
"""
import json
import subprocess
import sys
import tempfile
import unittest
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path

from freight_audit.audit import Auditor
from freight_audit.config import make_config
from freight_audit.evaluate import evaluate
from freight_audit.generator import generate
from freight_audit.history import assign_event_ids, sync_current_status
from freight_audit.pipeline import HISTORIES, run
from freight_audit.settlement import simulate_carrier_responses
from freight_audit.validate import validate
from tests.helpers import built_world

ROOT = Path(__file__).resolve().parents[2]


def finish(t, gt, cfg):
    """Everything pipeline.build() does after generate(), for worlds we tamper with first."""
    a = Auditor(t, cfg)
    a.audit_all()
    a.decide()
    gt += simulate_carrier_responses(t, cfg)
    a.reconcile_credits()
    a.close_disputed_invoices()
    for table, hist, key, prefix in HISTORIES:
        assign_event_ids(t[hist], prefix, key)
        sync_current_status(t[table], t[hist], key)
    return t, gt


class TestStarterRun(unittest.TestCase):
    """The default starter dataset, exactly as `python -m freight_audit run --scale starter` makes it."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name) / "starter"
        cls.res = run(make_config("starter"), cls.out, quiet=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_all_168_checks_pass(self):
        v = self.res["validation"]
        self.assertEqual((v["overall"], v["checks_run"], v["checks_passed"]), ("PASS", 168, 168))

    def test_known_results_for_seed_20261022(self):
        d, b = self.res["metrics"]["detection"], self.res["metrics"]["business"]
        self.assertEqual((d["overall"]["planted"], d["overall"]["true_positive"]), (107, 107))
        self.assertEqual(b["invoices_audited"], 750)
        self.assertEqual(b["total_billed"], 321010.27)
        self.assertEqual(b["savings_total"], 13725.82)

    def test_reports_agree_with_each_other(self):
        metrics = json.loads((self.out / "reports" / "audit_metrics.json").read_text(encoding="utf-8"))
        summary = (self.out / "reports" / "summary.md").read_text(encoding="utf-8")
        self.assertIn(f"{metrics['business']['invoices_audited']:,} carrier invoices were audited", summary)
        manifest = json.loads((self.out / "manifest.json").read_text(encoding="utf-8"))
        rows = {f["path"]: f["rows"] for f in manifest["files"]}
        self.assertEqual(rows["data/csv/freight_invoice.csv"], 750)

class TestCommandLine(unittest.TestCase):
    """Runs `python -m freight_audit ...` as a real separate process."""

    def cli(self, *args, cwd=ROOT):
        return subprocess.run([sys.executable, "-m", "freight_audit", *args], cwd=cwd,
                              capture_output=True, text=True, timeout=300)

    def test_run_and_reproduce(self):
        with tempfile.TemporaryDirectory() as d:
            args = ["--scale", "starter", "--out", d]          # temporary folder: real output/ untouched
            r = self.cli("run", *args)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("Validation : PASS", r.stdout)
            r = self.cli("reproduce", *args)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("IDENTICAL", r.stdout)
            self.assertIn("Matches the published output folder: True", r.stdout)

    def test_help_lists_the_commands(self):
        r = self.cli("--help")
        self.assertEqual(r.returncode, 0)
        for cmd in ("run", "reproduce", "all"):
            self.assertIn(cmd, r.stdout)


class TestTraceability(unittest.TestCase):
    """Every planted error can be followed through every table it touches."""

    @classmethod
    def setUpClass(cls):
        cls.cfg, cls.t, cls.gt = built_world()

    def test_answer_key_to_exception_to_line_to_shipment_to_delivery(self):
        lines = {l["line_id"]: l for l in self.t["invoice_line"]}
        ships = {s["shipment_id"] for s in self.t["shipment"]}
        delivered = {d["shipment_id"] for d in self.t["delivery_record"]}
        by_inv = defaultdict(list)
        for e in self.t["audit_exception"]:
            by_inv[e["invoice_id"]].append(e)
        for g in self.gt:
            if g["entity_type"] != "INVOICE":
                continue
            ex = [e for e in by_inv[g["entity_id"]] if e["exception_type"] == g["issue_type"]]
            self.assertTrue(ex, g)
            for e in ex:
                if e["line_id"]:
                    self.assertEqual(lines[e["line_id"]]["invoice_id"], g["entity_id"])
                if g["issue_type"] != "UNKNOWN_SHIPMENT":
                    self.assertIn(e["shipment_id"], ships)
                    self.assertIn(e["shipment_id"], delivered)

    def test_credit_note_to_exception_to_invoice(self):
        exc = {e["exception_id"]: e for e in self.t["audit_exception"]}
        inv = {i["invoice_id"]: i for i in self.t["freight_invoice"]}
        for cn in self.t["credit_note"]:
            e = exc[cn["exception_id"]]
            self.assertEqual(e["invoice_id"], cn["invoice_id"])
            self.assertEqual(inv[cn["invoice_id"]]["carrier_id"], cn["carrier_id"])

    def test_every_record_has_a_complete_history(self):
        for table, hist, key, _ in HISTORIES:
            seen = {h[key] for h in self.t[hist]}
            self.assertEqual(seen, {r[key] for r in self.t[table]}, table)


class TestNegativeScenarios(unittest.TestCase):
    """The system must notice when something is wrong."""

    def test_corrupted_data_fails_validation_and_names_the_records(self):
        cfg, t, gt = built_world()
        line = next(l for l in t["invoice_line"] if l["charge_type"] == "BASE")
        line["amount"] += D("10.00")
        inv = next(i for i in t["freight_invoice"] if i["current_status"] == "PAID")
        inv["invoice_date"] -= timedelta(days=60)
        t["carrier"].append(dict(t["carrier"][0]))
        s = validate(t, gt, cfg).summary()
        failed = {c["name"]: c["examples"] for c in s["checks"] if c["status"] == "FAIL"}
        self.assertEqual(s["overall"], "FAIL")
        self.assertIn(line["invoice_id"], failed["Invoice total = sum of its lines"])
        self.assertIn(inv["invoice_id"], failed["Dates: delivery <= invoice <= audit < credit note"])
        self.assertTrue(any("carrier: primary key" in n for n in failed))

    def test_lenient_detective_misses_errors_recall_drops_precision_holds(self):
        cfg = make_config("starter", carriers=6, shipments=400, invoices=300, lanes=30, tolerance=D("40"))
        t, gt, _ = generate(cfg)
        t, gt = finish(t, gt, cfg)
        o = evaluate(t, gt, cfg)["detection"]["overall"]
        self.assertLess(o["recall"], 1.0)
        self.assertEqual(o["precision"], 1.0)
        self.assertGreater(o["false_negative"], 0)

    def test_wrong_contract_data_causes_false_alarms_precision_drops_recall_holds(self):
        cfg = make_config("starter", carriers=6, shipments=400, invoices=300, lanes=30)
        t, gt, _ = generate(cfg)
        card = next(c for c in t["rate_card"] if c["version"] == "2026-01")
        card["fuel_surcharge_pct"] += D("1.0")              # contract typo after the bills were made
        t, gt = finish(t, gt, cfg)
        d = evaluate(t, gt, cfg)["detection"]
        self.assertLess(d["overall"]["precision"], 1.0)
        self.assertEqual(d["overall"]["recall"], 1.0)
        self.assertGreater(d["clean_invoices_falsely_flagged"], 0)


if __name__ == "__main__":
    unittest.main()
