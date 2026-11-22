"""Command line:

    python -m freight_audit run --scale starter        # small, ~2 seconds
    python -m freight_audit run --scale full           # 100 carriers / 20k shipments / 15k invoices
    python -m freight_audit reproduce --scale starter  # prove same seed -> identical files
    python -m freight_audit all                        # starter + full + both reproducibility checks
"""
import argparse
import sys
from pathlib import Path

from .config import DEFAULT_SEED, SCALES, make_config
from .pipeline import reproduce, run

ROOT = Path(__file__).resolve().parent.parent


def _print_result(res):
    v, m = res["validation"], res["metrics"]
    d, b = m["detection"]["overall"], m["business"]
    print("")
    print(f"  Validation : {v['overall']} ({v['checks_passed']}/{v['checks_run']} checks passed)")
    print(f"  Detection  : precision {d['precision']:.1%}, recall {d['recall']:.1%} "
          f"({d['true_positive']} of {d['planted']} planted problems found)")
    print(f"  Money      : billed ${b['total_billed']:,.2f}, saved ${b['savings_total']:,.2f}")
    print(f"  Output     : {res['out']}")
    print(f"  Read first : {Path(res['out']) / 'reports' / 'summary.md'}")
    for c in v["checks"]:
        if c["status"] == "FAIL":
            print(f"  FAILED CHECK: {c['name']} -> {c['examples'][:3]}")


def _print_repro(rep):
    status = "IDENTICAL" if rep["all_identical"] else "DIFFERENT"
    print(f"  Reproducibility ({rep['scale']}): {rep['files_compared']} files compared twice -> {status}")
    if rep["matches_published_output"] is not None:
        print(f"  Matches the published output folder: {rep['matches_published_output']}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m freight_audit",
                                description="Freight invoice audit: generate, audit, validate.")
    sub = p.add_subparsers(dest="command")
    for name in ("run", "reproduce", "all"):
        s = sub.add_parser(name)
        s.add_argument("--scale", choices=list(SCALES), default="starter")
        s.add_argument("--seed", type=int, default=DEFAULT_SEED)
        s.add_argument("--out", default=str(ROOT / "output"), help="output root folder")
    args = p.parse_args(argv)
    if not args.command:
        p.print_help()
        return 0

    scales = list(SCALES) if args.command == "all" else [args.scale]
    ok = True
    for scale in scales:
        cfg = make_config(scale, args.seed)
        out = Path(args.out) / scale
        if args.command in ("run", "all"):
            res = run(cfg, out)
            _print_result(res)
            ok &= res["validation"]["overall"] == "PASS"
        if args.command in ("reproduce", "all"):
            rep = reproduce(cfg, out)
            _print_repro(rep)
            ok &= rep["all_identical"]
        print("")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
