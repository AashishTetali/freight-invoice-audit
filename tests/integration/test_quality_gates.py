"""Integration tests: quality gates on a whole generated + audited world.

These run the real pipeline (generate -> audit -> settle -> validate -> grade -> export)
and check the acceptance criteria of the brief end to end.
"""
import hashlib
import inspect
import json
import re
import tempfile
import unittest
from decimal import Decimal as D
from pathlib import Path

from freight_audit import audit as audit_module
from freight_audit.config import make_config
from freight_audit.export import jsonable
from freight_audit.pipeline import build, reproduce, run
from freight_audit.schema import INJECTED_ISSUE_TYPES
from freight_audit.evaluate import evaluate
from freight_audit.validate import validate

SMALL = dict(carriers=6, shipments=400, invoices=300, lanes=30)


def fingerprint(t, gt):
    blob = json.dumps({"t": t, "gt": gt}, default=jsonable, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


class TestPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = make_config("starter", **SMALL)
        cls.t, cls.gt = build(cls.cfg, quiet=True)
        cls.validation = validate(cls.t, cls.gt, cls.cfg).summary()
        cls.metrics = evaluate(cls.t, cls.gt, cls.cfg)

    def test_all_validation_checks_pass(self):
        failed = [c["name"] for c in self.validation["checks"] if c["status"] == "FAIL"]
        self.assertEqual(failed, [])

    def test_every_edge_case_is_planted(self):
        planted = {g["issue_type"] for g in self.gt}
        self.assertEqual(planted, set(INJECTED_ISSUE_TYPES))

    def test_auditor_finds_every_planted_problem_without_false_alarms(self):
        overall = self.metrics["detection"]["overall"]
        self.assertEqual(overall["false_negative"], 0)
        self.assertEqual(overall["false_positive"], 0)
        self.assertEqual(self.metrics["detection"]["clean_invoices_falsely_flagged"], 0)

    def test_decisions_and_amounts_match_answer_key(self):
        self.assertEqual(self.metrics["detection"]["decision_accuracy"], 1.0)
        self.assertEqual(self.metrics["detection"]["variance_accuracy"], 1.0)

    def test_id_formats(self):
        patterns = {"carrier": ("carrier_id", r"^CAR-\d{6}$"),
                    "rate_card": ("rate_card_id", r"^RATE-CAR-\d{6}-2026-0[12]$"),
                    "shipment": ("shipment_id", r"^SHP-2026-\d{6}$"),
                    "freight_invoice": ("invoice_id", r"^FINV-2026-\d{6}$"),
                    "invoice_line": ("line_id", r"^FLINE-FINV-2026-\d{6}-\d{2}$"),
                    "audit_exception": ("exception_id", r"^FEX-2026-\d{6}$")}
        for table, (col, rx) in patterns.items():
            for row in self.t[table]:
                self.assertRegex(row[col], rx)
        for line in self.t["invoice_line"]:
            self.assertTrue(line["line_id"].startswith(f"FLINE-{line['invoice_id']}-"))

    def test_duplicate_bills_rejected(self):
        dup = {g["entity_id"] for g in self.gt if g["issue_type"] == "DUPLICATE_INVOICE"}
        status = {i["invoice_id"]: i["current_status"] for i in self.t["freight_invoice"]}
        self.assertTrue(dup)
        self.assertTrue(all(status[i] == "REJECTED" for i in dup))

    def test_credits_never_exceed_approved(self):
        approved = {e["exception_id"]: e["approved_amount"] for e in self.t["audit_exception"]}
        applied = {}
        for cn in self.t["credit_note"]:
            if cn["current_status"] == "APPLIED":
                applied[cn["exception_id"]] = applied.get(cn["exception_id"], D(0)) + cn["credit_amount"]
        self.assertTrue(all(v <= approved[k] for k, v in applied.items()))
        self.assertTrue(any(cn["current_status"] == "HELD" for cn in self.t["credit_note"]))

    def test_auditor_never_reads_ground_truth(self):
        code = inspect.getsource(audit_module).split('"""', 2)[2]    # skip the module docstring
        self.assertNotIn("ground_truth", code)
        self.assertNotIn("gt", re.findall(r"\b\w+\b", code))

    def test_same_seed_same_data_and_different_seed_different_data(self):
        again = build(self.cfg, quiet=True)
        self.assertEqual(fingerprint(self.t, self.gt), fingerprint(*again))
        other = build(make_config("starter", seed=1, **SMALL), quiet=True)
        self.assertNotEqual(fingerprint(self.t, self.gt), fingerprint(*other))


class TestFilesOnDisk(unittest.TestCase):
    def test_export_and_byte_identical_rerun(self):
        cfg = make_config("starter", **SMALL)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "starter"
            res = run(cfg, out, quiet=True)
            self.assertEqual(res["validation"]["overall"], "PASS")
            for rel in ["data/csv/freight_invoice.csv", "data/json/audit_exception.json",
                        "data/sql/schema.sql", "data/sql/seed.sql",
                        "ground_truth/ground_truth.csv", "reports/validation_summary.json",
                        "reports/audit_metrics.json", "manifest.json", "DATA_DICTIONARY.md"]:
                self.assertTrue((out / rel).exists(), rel)
            # the answer key is NOT inside the data folder
            self.assertFalse(list((out / "data").rglob("ground_truth*")))
            rep = reproduce(cfg, out, quiet=True)
            self.assertTrue(rep["all_identical"])
            self.assertTrue(rep["matches_published_output"])


if __name__ == "__main__":
    unittest.main()
