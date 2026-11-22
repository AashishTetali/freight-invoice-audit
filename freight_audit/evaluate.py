"""Scores the audit engine against the answer key and computes business numbers.

Precision = of the problems we flagged, how many were real?
Recall    = of the real (planted) problems, how many did we catch?
"""
from collections import Counter, defaultdict

from .pricing import ZERO
from .schema import DERIVED_EXCEPTION_TYPES, INJECTED_ISSUE_TYPES


def _ratio(a, b):
    return round(a / b, 4) if b else None


def _money(x):
    return float(x)


def evaluate(t, gt, cfg):
    exceptions = t["audit_exception"]
    detected = set()
    for ex in exceptions:
        if ex["exception_type"] in DERIVED_EXCEPTION_TYPES:
            continue                       # knock-on effects are not separate errors
        entity = ex["credit_note_id"] if ex["exception_type"] == "CREDIT_EXCEEDS_EXCEPTION" \
            else ex["invoice_id"]
        detected.add((entity, ex["exception_type"]))
    truth = {(g["entity_id"], g["issue_type"]) for g in gt}

    per_type = {}
    for kind in list(INJECTED_ISSUE_TYPES) + sorted({k for _, k in detected} - set(INJECTED_ISSUE_TYPES)):
        tr = {x for x in truth if x[1] == kind}
        de = {x for x in detected if x[1] == kind}
        tp, fp, fn = len(tr & de), len(de - tr), len(tr - de)
        p, r = _ratio(tp, tp + fp), _ratio(tp, tp + fn)
        per_type[kind] = dict(planted=len(tr), detected=len(de), true_positive=tp,
                              false_positive=fp, false_negative=fn, precision=p, recall=r,
                              f1=round(2 * p * r / (p + r), 4) if p and r else None,
                              missed_examples=sorted(e for e, _ in tr - de)[:5],
                              false_alarm_examples=sorted(e for e, _ in de - tr)[:5])
    tp, fp, fn = len(truth & detected), len(detected - truth), len(truth - detected)

    # Decisions: did the rules lead to the decision the answer key expects?
    inv = {i["invoice_id"]: i for i in t["freight_invoice"]}
    cns = {c["credit_note_id"]: c for c in t["credit_note"]}
    first_decision = {}
    for e in t["exception_status_history"]:
        if e["from_status"] == "OPEN":
            first_decision[e["exception_id"]] = e["to_status"]
    ex_by_key = defaultdict(list)
    for ex in exceptions:
        ex_by_key[(ex["invoice_id"], ex["exception_type"])].append(ex)
    audits = {a["invoice_id"]: a for a in t["invoice_audit"]}
    over_ex = {ex["credit_note_id"]: ex for ex in exceptions if ex["credit_note_id"]}
    dec_ok = var_ok = 0
    dec_wrong, var_wrong = [], []
    for g in gt:
        if g["entity_type"] == "CREDIT_NOTE":
            ok_dec = cns[g["entity_id"]]["current_status"] == "HELD"
            ex = over_ex.get(g["entity_id"])
            ok_var = ex is not None and ex["variance_amount"] == g["expected_variance_amount"]
        elif g["expected_decision"] == "REJECT_INVOICE":
            ok_dec = audits[g["entity_id"]]["audit_result"] == "REJECTED"
            ok_var = audits[g["entity_id"]]["variance_total"] == g["expected_variance_amount"]
        else:
            exs = ex_by_key.get((g["entity_id"], g["issue_type"]), [])
            want = "DISPUTED" if g["expected_decision"] == "DISPUTE" else "WAIVED"
            ok_dec = bool(exs) and first_decision.get(exs[0]["exception_id"]) == want
            ok_var = audits[g["entity_id"]]["variance_total"] == g["expected_variance_amount"]
        dec_ok += ok_dec
        var_ok += ok_var
        if not ok_dec:
            dec_wrong.append(g["gt_id"])
        if not ok_var:
            var_wrong.append(g["gt_id"])

    labelled = {g["entity_id"] for g in gt if g["entity_type"] == "INVOICE"}
    flagged = {ex["invoice_id"] for ex in exceptions}
    clean = [i for i in inv if i not in labelled]
    false_alarm_clean = [i for i in clean if i in flagged]

    # ---------------- business outcomes
    a_rows = t["invoice_audit"]
    real = [ex for ex in exceptions if ex["exception_type"] != "CREDIT_EXCEEDS_EXCEPTION"]
    by_type = defaultdict(lambda: [0, ZERO])
    for ex in exceptions:
        by_type[ex["exception_type"]][0] += 1
        by_type[ex["exception_type"]][1] += ex["variance_amount"]
    statuses = Counter(i["current_status"] for i in t["freight_invoice"])
    ex_status = Counter(ex["current_status"] for ex in exceptions)
    rejected_value = sum((a["billed_total"] for a in a_rows if a["audit_result"] == "REJECTED"), ZERO)
    line_over = sum((ex["variance_amount"] for ex in real if ex["variance_amount"] > 0
                     and ex["exception_type"] not in ("UNKNOWN_SHIPMENT", "DUPLICATE_INVOICE",
                                                      "CARRIER_MISMATCH")), ZERO)
    disputed = sum((ex["approved_amount"] for ex in real), ZERO)
    credited = sum((c["credit_amount"] for c in t["credit_note"] if c["current_status"] == "APPLIED"), ZERO)
    held = sum((c["credit_amount"] for c in t["credit_note"] if c["current_status"] == "HELD"), ZERO)
    waived = sum((ex["variance_amount"] for ex in real if ex["current_status"] == "WAIVED"), ZERO)

    per_carrier = defaultdict(lambda: dict(invoices=0, with_exceptions=0, billed=ZERO, overbilled=ZERO))
    names = {c["carrier_id"]: c["carrier_name"] for c in t["carrier"]}
    for a in a_rows:
        c = per_carrier[inv[a["invoice_id"]]["carrier_id"]]
        c["invoices"] += 1
        c["billed"] += a["billed_total"]
        if a["audit_result"] != "CLEAN":
            c["with_exceptions"] += 1
            c["overbilled"] += max(a["variance_total"], ZERO)
    carriers = sorted(
        (dict(carrier_id=cid, carrier_name=names[cid], invoices=v["invoices"],
              invoices_with_exceptions=v["with_exceptions"],
              exception_rate=_ratio(v["with_exceptions"], v["invoices"]),
              billed=_money(v["billed"]), overbilled=_money(v["overbilled"]))
         for cid, v in per_carrier.items()),
        key=lambda c: (-(c["exception_rate"] or 0), c["carrier_id"]))

    total_billed = sum((a["billed_total"] for a in a_rows), ZERO)
    return dict(
        detection=dict(
            overall=dict(planted=len(truth), detected=len(detected), true_positive=tp,
                         false_positive=fp, false_negative=fn,
                         precision=_ratio(tp, tp + fp), recall=_ratio(tp, tp + fn)),
            clean_invoices=len(clean), clean_invoices_falsely_flagged=len(false_alarm_clean),
            false_alarm_rate_on_clean=_ratio(len(false_alarm_clean), len(clean)),
            decision_accuracy=_ratio(dec_ok, len(gt)), decision_mismatches=dec_wrong[:10],
            variance_accuracy=_ratio(var_ok, len(gt)), variance_mismatches=var_wrong[:10],
            per_issue_type=per_type),
        business=dict(
            invoices_audited=len(a_rows),
            invoices_clean=sum(a["audit_result"] == "CLEAN" for a in a_rows),
            invoices_with_exceptions=sum(a["audit_result"] == "EXCEPTIONS" for a in a_rows),
            invoices_rejected=sum(a["audit_result"] == "REJECTED" for a in a_rows),
            clean_rate=_ratio(sum(a["audit_result"] == "CLEAN" for a in a_rows), len(a_rows)),
            total_billed=_money(total_billed),
            rejected_invoice_value=_money(rejected_value),
            line_overbilling_found=_money(line_over),
            disputed_amount=_money(disputed),
            credits_applied=_money(credited),
            credits_held_over_limit=_money(held),
            waived_amount=_money(waived),
            savings_total=_money(rejected_value + credited),
            savings_pct_of_billed=_ratio(float(rejected_value + credited), float(total_billed)),
            dispute_recovery_rate=_ratio(float(credited), float(disputed)),
            invoice_status=dict(sorted(statuses.items())),
            exception_status=dict(sorted(ex_status.items())),
            exceptions_by_type={k: dict(count=v[0], variance=_money(v[1]))
                                for k, v in sorted(by_type.items())},
            shipments=len(t["shipment"]),
            shipments_not_yet_invoiced=len(t["shipment"]) - len({a["shipment_id"] for a in a_rows
                                                                if a["shipment_id"]}),
        ),
        carriers_by_exception_rate=carriers,
    )
