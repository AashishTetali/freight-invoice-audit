"""Shared helpers for the unit and integration tests.

Every helper builds a small, fresh world, so tests never share or change each
other's data. A small world (6 carriers, 400 shipments, 300 invoices) builds in
well under a second and still contains every planted error type.
"""
import copy
import functools
from collections import Counter
from datetime import timedelta
from decimal import Decimal

from freight_audit.audit import Auditor
from freight_audit.config import make_config
from freight_audit.generator import generate
from freight_audit.pipeline import build
from freight_audit.pricing import Index

SMALL = dict(carriers=6, shipments=400, invoices=300, lanes=30)
D = Decimal


def small_cfg(**overrides):
    return make_config("starter", **{**SMALL, **overrides})


@functools.lru_cache(maxsize=1)
def _built_once():
    cfg = small_cfg()
    t, gt = build(cfg, quiet=True)
    return cfg, t, gt


def built_world():
    """A fully processed world (generated, audited, settled). Fresh deep copy every call."""
    cfg, t, gt = _built_once()
    return cfg, copy.deepcopy(t), copy.deepcopy(gt)


def generated_world(**overrides):
    """A world straight out of the generator, before any audit. Fresh every call."""
    cfg = small_cfg(**overrides)
    t, gt, hidden = generate(cfg)
    return cfg, t, gt, hidden


def lines_of(t, invoice_id):
    return sorted((l for l in t["invoice_line"] if l["invoice_id"] == invoice_id), key=lambda l: l["line_no"])


def exceptions_for(t, invoice_id):
    return [e for e in t["audit_exception"] if e["invoice_id"] == invoice_id]


def types_for(t, invoice_id):
    return sorted(e["exception_type"] for e in exceptions_for(t, invoice_id))


def clean_invoice(t, gt, cfg=None, where=None):
    """A bill with no planted error, a real shipment, billed only once, matching `where`."""
    planted = {g["entity_id"] for g in gt}
    ships = {s["shipment_id"]: s for s in t["shipment"]}
    billed = Counter(i["shipment_ref"] for i in t["freight_invoice"])
    idx = Index(t)
    for inv in t["freight_invoice"]:
        if inv["invoice_id"] in planted or inv["shipment_ref"] not in ships or billed[inv["shipment_ref"]] > 1:
            continue
        ctx = dict(inv=inv, ship=ships[inv["shipment_ref"]], exp=idx.expected(inv["shipment_ref"]),
                   lines=lines_of(t, inv["invoice_id"]), idx=idx)
        if where is None or where(ctx):
            return ctx
    raise LookupError("no clean invoice matches the condition")


def run_audit(t, cfg, decide=False):
    a = Auditor(t, cfg)
    a.audit_all()
    if decide:
        a.decide()
    return a


def next_day(d, n=1):
    return d + timedelta(days=n)
