"""Runs the whole workflow end to end:

  generate world -> audit invoices -> analyst decisions -> carrier credits
  -> reconcile credits -> close invoices -> validate -> score -> export
"""
import platform
import shutil
import time
from dataclasses import asdict
from pathlib import Path

from .audit import Auditor
from .evaluate import evaluate
from .export import export_all, file_entries, write_json_report
from .generator import generate
from .history import assign_event_ids, sync_current_status
from .settlement import simulate_carrier_responses
from .validate import validate

HISTORIES = [("shipment", "shipment_status_history", "shipment_id", "SEV"),
             ("freight_invoice", "invoice_status_history", "invoice_id", "IEV"),
             ("audit_exception", "exception_status_history", "exception_id", "EEV"),
             ("credit_note", "credit_note_status_history", "credit_note_id", "CEV")]


def log(msg, quiet):
    if not quiet:
        print(msg, flush=True)


def build(cfg, quiet=False):
    """Creates every table in memory. Returns (tables, ground_truth)."""
    t0 = time.time()
    t, gt, _hidden = generate(cfg)
    log(f"  [1/4] generated world: {len(t['carrier'])} carriers, {len(t['shipment'])} shipments, "
        f"{len(t['freight_invoice'])} invoices ({time.time() - t0:.1f}s)", quiet)
    auditor = Auditor(t, cfg)          # NOTE: the auditor is never given `gt`
    auditor.audit_all()
    auditor.decide()
    log(f"  [2/4] audited invoices: {len(t['audit_exception'])} exceptions raised "
        f"({time.time() - t0:.1f}s)", quiet)
    gt += simulate_carrier_responses(t, cfg)
    auditor.reconcile_credits()
    auditor.close_disputed_invoices()
    log(f"  [3/4] settled disputes: {len(t['credit_note'])} credit notes "
        f"({time.time() - t0:.1f}s)", quiet)
    for table, hist, key, prefix in HISTORIES:
        assign_event_ids(t[hist], prefix, key)
        sync_current_status(t[table], t[hist], key)
    t["audit_exception"].sort(key=lambda e: e["exception_id"])
    gt.sort(key=lambda g: (g["entity_type"], g["entity_id"], g["issue_type"]))
    for i, g in enumerate(gt, 1):
        g["gt_id"] = f"GT-{i:06d}"
    return t, gt


def summary_markdown(cfg, metrics, validation, t):
    b, d = metrics["business"], metrics["detection"]
    pct = lambda x: "n/a" if x is None else f"{x * 100:.1f}%"
    usd = lambda x: f"${x:,.2f}"
    lines = [f"# Freight invoice audit - results ({cfg.scale}, seed {cfg.seed})", "",
             "## What happened", "",
             f"* {b['invoices_audited']:,} carrier invoices were audited against contracts and "
             f"delivery records ({b['shipments']:,} shipments in total).",
             f"* {b['invoices_clean']:,} were correct ({pct(b['clean_rate'])}), "
             f"{b['invoices_with_exceptions']:,} had line-level problems and "
             f"{b['invoices_rejected']:,} were rejected outright (duplicates / unknown shipments).",
             f"* Total billed {usd(b['total_billed'])}. Rejected bills worth "
             f"{usd(b['rejected_invoice_value'])}; line overbilling found "
             f"{usd(b['line_overbilling_found'])}; disputed {usd(b['disputed_amount'])}; "
             f"credits applied {usd(b['credits_applied'])}; over-limit credits held "
             f"{usd(b['credits_held_over_limit'])}.",
             f"* Money saved (rejected bills + credits): **{usd(b['savings_total'])}** "
             f"({pct(b['savings_pct_of_billed'])} of billed).", "",
             "## How well the audit engine found the planted problems", "",
             f"Overall precision **{pct(d['overall']['precision'])}**, recall "
             f"**{pct(d['overall']['recall'])}**; false alarms on clean invoices: "
             f"{d['clean_invoices_falsely_flagged']} of {d['clean_invoices']:,}. "
             f"Decisions matching the rules: {pct(d['decision_accuracy'])}; "
             f"$ variance matching the answer key: {pct(d['variance_accuracy'])}.", "",
             "| Issue type | Planted | Detected | Precision | Recall |", "|---|---|---|---|---|"]
    for k, v in d["per_issue_type"].items():
        lines.append(f"| {k} | {v['planted']} | {v['detected']} | {pct(v['precision'])} | "
                     f"{pct(v['recall'])} |")
    lines += ["", "## Exceptions raised", "", "| Type | Count | Total variance |", "|---|---|---|"]
    for k, v in b["exceptions_by_type"].items():
        lines.append(f"| {k} | {v['count']} | {usd(v['variance'])} |")
    lines += ["", "## Example exceptions (one per type)", ""]
    seen = set()
    for ex in t["audit_exception"]:
        if ex["exception_type"] not in seen:
            seen.add(ex["exception_type"])
            lines.append(f"* `{ex['exception_id']}` **{ex['exception_type']}** on "
                         f"`{ex['line_id'] or ex['invoice_id']}`: {ex['detail']} "
                         f"(variance ${ex['variance_amount']})")
    lines += ["", "## Carriers with the highest exception rate", "",
              "| Carrier | Invoices | With exceptions | Rate | Overbilled |", "|---|---|---|---|---|"]
    for c in metrics["carriers_by_exception_rate"][:10]:
        lines.append(f"| {c['carrier_id']} {c['carrier_name']} | {c['invoices']} | "
                     f"{c['invoices_with_exceptions']} | {pct(c['exception_rate'])} | "
                     f"{usd(c['overbilled'])} |")
    lines += ["", "## Data validation", "",
              f"**{validation['overall']}**: {validation['checks_passed']} of "
              f"{validation['checks_run']} checks passed."]
    failed = [c for c in validation["checks"] if c["status"] == "FAIL"]
    lines += [f"* FAILED: {c['name']} ({c['failed']}) e.g. {c['examples'][:3]}" for c in failed]
    return "\n".join(lines) + "\n"


def run(cfg, out_dir, quiet=False):
    out = Path(out_dir)
    if out.exists():
        shutil.rmtree(out)
    start = time.time()
    log(f"Building '{cfg.scale}' dataset with seed {cfg.seed} -> {out}", quiet)
    t, gt = build(cfg, quiet)
    rep = validate(t, gt, cfg)
    metrics = evaluate(t, gt, cfg)
    rows = export_all(out, t, gt)
    validation = rep.summary()
    write_json_report(out / "reports" / "validation_summary.json", validation)
    write_json_report(out / "reports" / "audit_metrics.json", metrics)
    (out / "reports" / "summary.md").write_text(summary_markdown(cfg, metrics, validation, t),
                                                encoding="utf-8", newline="\n")
    manifest = dict(
        project="Freight invoice audit - synthetic dataset and audit results",
        generator_command=f"python -m freight_audit run --scale {cfg.scale} --seed {cfg.seed}",
        seed=cfg.seed, scale=cfg.scale,
        config={k: (str(v) if not isinstance(v, (int, float, str, dict)) else v)
                for k, v in asdict(cfg).items()},
        python_version=platform.python_version(),
        external_data_sources=[],
        provenance=("100% synthetic. No external datasets were downloaded or used and no personal "
                    "or confidential data is included. Carrier names are invented. City "
                    "coordinates are approximate public geographic facts typed in by hand, used "
                    "only to estimate distances."),
        ground_truth="ground_truth/ (answer key - never read by the audit engine)",
        reproducibility_note="Every file except this manifest is byte-identical across runs "
                             "with the same seed.",
        files=file_entries(out, rows))
    write_json_report(out / "manifest.json", manifest)
    log(f"  [4/4] validated and exported ({time.time() - start:.1f}s total)", quiet)
    return dict(out=str(out), validation=validation, metrics=metrics)


SKIP = ("manifest.json", "reports/reproducibility.json")


def reproduce(cfg, out_dir, quiet=False):
    """Generates the dataset twice from scratch and compares SHA-256 checksums."""
    out = Path(out_dir)
    tmp = out.parent / f"_repro_{cfg.scale}"
    runs = []
    for k in (1, 2):
        log(f"Reproducibility run {k} of 2...", quiet)
        run(cfg, tmp / f"run_{k}", quiet=True)
        runs.append({e["path"]: e for e in file_entries(tmp / f"run_{k}", {})
                     if e["path"] not in SKIP})
    a, b = runs
    files = []
    for path in sorted(set(a) | set(b)):
        files.append(dict(path=path, rows=None, sha256_run_1=a.get(path, {}).get("sha256"),
                          sha256_run_2=b.get(path, {}).get("sha256"),
                          identical=a.get(path, {}).get("sha256") == b.get(path, {}).get("sha256")))
    main = {}
    if (out / "manifest.json").exists():
        main = {e["path"]: e for e in file_entries(out, {}) if e["path"] not in SKIP}
    matches_main = bool(main) and all(main.get(p, {}).get("sha256") == e["sha256"]
                                      for p, e in a.items())
    report = dict(seed=cfg.seed, scale=cfg.scale, files_compared=len(files),
                  all_identical=all(f["identical"] for f in files),
                  matches_published_output=matches_main if main else None,
                  excluded=list(SKIP), files=files)
    shutil.rmtree(tmp, ignore_errors=True)
    if out.exists():
        (out / "reports").mkdir(parents=True, exist_ok=True)
        write_json_report(out / "reports" / "reproducibility.json", report)
    return report
