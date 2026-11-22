"""Simulates the CARRIER side of a dispute (part of the synthetic world, not the
auditor): for each disputed exception the carrier either credits it in full,
credits part of it, declines, or - the planted anomaly - credits MORE than was
approved. Over-credits are written to the ground truth.
"""
from datetime import timedelta

from .generator import dec, rng_for
from .history import transition
from .pricing import money


def simulate_carrier_responses(t, cfg):
    r = rng_for(cfg, "carrier_responses")
    disputed_on = {}
    for e in t["exception_status_history"]:
        if e["to_status"] == "DISPUTED":
            disputed_on[e["exception_id"]] = e["event_date"]
    disputed = [ex for ex in t["audit_exception"] if ex["exception_id"] in disputed_on]

    full, partial, decline = cfg.credit_full_rate, cfg.credit_partial_rate, cfg.credit_decline_rate
    plan = []
    for ex in disputed:
        u = r.random()
        plan.append("FULL" if u < full else "PARTIAL" if u < full + partial
                    else "DECLINE" if u < full + partial + decline else "OVER")
    # make sure a few over-credits exist even in small datasets
    fulls = [i for i, p in enumerate(plan) if p == "FULL"]
    missing = cfg.min_cases_per_type - plan.count("OVER")
    for i in sorted(r.sample(fulls, min(max(missing, 0), len(fulls)))):
        plan[i] = "OVER"

    drafts, gt = [], []
    for ex, action in zip(disputed, plan):
        reply = disputed_on[ex["exception_id"]] + timedelta(days=r.randint(5, 25))
        approved = ex["approved_amount"]
        if action == "DECLINE":
            transition(ex, t["exception_status_history"], "exception_id", "CARRIER_DECLINED",
                       reply, "CARRIER", "Carrier rejected the dispute")
            continue
        if action == "FULL":
            amount = approved
        elif action == "PARTIAL":
            amount = money(approved * dec(r.uniform(0.4, 0.9), "0.01"))
        else:
            amount = approved + max(money(approved * dec(r.uniform(0.1, 0.5), "0.01")), money(1))
        drafts.append(dict(ex=ex, issued=reply, amount=amount, over=action == "OVER"))

    drafts.sort(key=lambda d: (d["issued"], d["ex"]["exception_id"]))
    notes, hist = [], t["credit_note_status_history"]
    for n, d in enumerate(drafts, 1):
        ex = d["ex"]
        cn = dict(credit_note_id=f"CN-{cfg.year}-{n:06d}", exception_id=ex["exception_id"],
                  invoice_id=ex["invoice_id"], carrier_id=None, issued_date=d["issued"],
                  credit_amount=d["amount"], current_status=None)
        notes.append(cn)
        transition(cn, hist, "credit_note_id", "ISSUED", d["issued"], "CARRIER",
                   f"Credit for {ex['exception_id']}")
        if d["over"]:
            gt.append(dict(gt_id=None, entity_type="CREDIT_NOTE", entity_id=cn["credit_note_id"],
                           issue_type="CREDIT_EXCEEDS_EXCEPTION", line_no=None,
                           expected_decision="HOLD_CREDIT",
                           expected_variance_amount=d["amount"] - ex["approved_amount"],
                           injection_detail=f"Credited ${d['amount']} against "
                                            f"${ex['approved_amount']} approved on "
                                            f"{ex['exception_id']}"))
    carrier_of = {i["invoice_id"]: i["carrier_id"] for i in t["freight_invoice"]}
    for cn in notes:
        cn["carrier_id"] = carrier_of[cn["invoice_id"]]
    t["credit_note"] = notes
    return gt
