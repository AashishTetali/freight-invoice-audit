"""Writes everything to disk: CSV, JSON, SQL (schema + seed), the data dictionary
and a manifest with SHA-256 checksums."""
import csv
import hashlib
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from .schema import GROUND_TRUTH, RELATIONSHIPS, SQL_TYPES, TABLES, TRANSITIONS


def fmt(v):
    if v is None:
        return ""
    if isinstance(v, date):
        return v.isoformat()
    return str(v)


def jsonable(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, date):
        return v.isoformat()
    return v


def sql_literal(v):
    if v is None:
        return "NULL"
    if isinstance(v, (int, Decimal)):
        return str(v)
    if isinstance(v, date):
        return f"'{v.isoformat()}'"
    return "'" + str(v).replace("'", "''") + "'"


def write_csv(path, table, rows):
    cols = [c.name for c in table.cols]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(cols)
        for r in rows:
            w.writerow([fmt(r.get(c)) for c in cols])


def write_json(path, table, rows):
    cols = [c.name for c in table.cols]
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump([{c: jsonable(r.get(c)) for c in cols} for r in rows], f, indent=1)
        f.write("\n")


# ------------------------------------------------------------------ SQL
def ddl(table):
    parts = []
    for c in table.cols:
        line = f"  {c.name} {SQL_TYPES[c.type]}"
        if c.required:
            line += " NOT NULL"
        if c.enum is not None:
            vals = ", ".join(sql_literal(v) for v in c.enum)
            line += f" CHECK ({c.name} IS NULL OR {c.name} IN ({vals}))"
        parts.append(line)
    parts.append(f"  PRIMARY KEY ({', '.join(table.pk)})")
    for c in table.cols:
        if c.unique and (c.name,) != table.pk:
            parts.append(f"  UNIQUE ({c.name})")
    for cols in table.unique_together:
        parts.append(f"  UNIQUE ({', '.join(cols)})")
    for c in table.cols:
        if c.fk:
            pt, pc = c.fk.split(".")
            parts.append(f"  FOREIGN KEY ({c.name}) REFERENCES {pt}({pc}) "
                         f"DEFERRABLE INITIALLY DEFERRED")
    return f"-- {table.desc}\nCREATE TABLE {table.name} (\n" + ",\n".join(parts) + "\n);\n"


def schema_sql():
    head = ("-- Freight invoice audit - database schema (ANSI SQL)\n"
            "-- Generated from freight_audit/schema.py. Load schema.sql, then seed.sql.\n"
            "PRAGMA foreign_keys = ON;\n\n")
    drops = "".join(f"DROP TABLE IF EXISTS {t.name};\n" for t in reversed(TABLES))
    return head + drops + "\n" + "\n".join(ddl(t) for t in TABLES)


def write_seed_sql(path, t, batch=500):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("-- Seed data. All foreign keys are checked at COMMIT.\n"
                "PRAGMA foreign_keys = ON;\nBEGIN TRANSACTION;\n")
        for table in TABLES:
            rows = t[table.name]
            cols = [c.name for c in table.cols]
            for i in range(0, len(rows), batch):
                chunk = rows[i:i + batch]
                values = ",\n".join("(" + ", ".join(sql_literal(r.get(c)) for c in cols) + ")"
                                    for r in chunk)
                f.write(f"INSERT INTO {table.name} ({', '.join(cols)}) VALUES\n{values};\n")
        f.write("COMMIT;\n")


# ------------------------------------------------------------------ data dictionary
def data_dictionary_md():
    out = ["# Data dictionary", "",
           "Generated from `freight_audit/schema.py` (the same definitions create the SQL schema "
           "and drive the validation checks). Money is in USD with 2 decimals, weights in pounds "
           "(lb), dimensions in inches, dates are ISO `YYYY-MM-DD`.", "",
           "## ID conventions", "",
           "| Entity | Format | Example |", "|---|---|---|",
           "| Carrier | `CAR-` + 6 digits | CAR-000001 |",
           "| Rate card | `RATE-<carrier-id>-<year>-<version>` | RATE-CAR-000001-2026-01 |",
           "| Shipment | `SHP-<year>-` + 6 digits | SHP-2026-000001 |",
           "| Freight invoice | `FINV-<year>-` + 6 digits | FINV-2026-000001 |",
           "| Invoice line | `FLINE-<invoice-id>-` + 2 digits | FLINE-FINV-2026-000001-01 |",
           "| Audit exception | `FEX-<year>-` + 6 digits | FEX-2026-000001 |",
           "| Credit note | `CN-<year>-` + 6 digits | CN-2026-000001 |",
           "| Others | LANE-000001, RLN-000001, WM-2026-000001, DLV-2026-000001, "
           "<shipment-id>-L1, SEV/IEV/EEV/CEV-0000001 (history events), GT-000001 | |", "",
           "IDs are zero-padded, never re-used and never changed.", "",
           "## Entity-relationship diagram", "", "```mermaid", "erDiagram"]
    for parent, child, note in RELATIONSHIPS:
        if "N:M via" in note:
            bridge = note.split("via ")[1]
            out.append(f"    {parent.upper()} ||--o{{ {bridge.upper()} : has")
            out.append(f"    {child.upper()} ||--o{{ {bridge.upper()} : in")
        elif parent != child:
            rel = "||--||" if note.startswith("1:1") else \
                "||--o|" if note.startswith("1:0..1") else "||--o{"
            out.append(f"    {parent.upper()} {rel} {child.upper()} : \"{note.split(' ')[0]}\"")
    out += ["```", "", "## Relationships and cardinality", "",
            "| Parent | Child | Cardinality / notes |", "|---|---|---|"]
    out += [f"| {p} | {c} | {n} |" for p, c, n in RELATIONSHIPS]
    out += ["", "## Lifecycle states", "",
            "Status changes are stored as new rows in the `*_status_history` tables; "
            "`current_status` is a convenience copy of the latest event.", ""]
    for entity, trans in TRANSITIONS.items():
        out.append(f"* **{entity}**: " + "; ".join(
            f"{frm or '(new)'} -> {' / '.join(sorted(to))}" for frm, to in trans.items()))
    out.append("")
    for table in list(TABLES) + [GROUND_TRUTH]:
        out += [f"## `{table.name}` ({table.group})", "", table.desc, "",
                f"Primary key: `{', '.join(table.pk)}`"
                + (f". Also unique: " + "; ".join(f"({', '.join(u)})" for u in table.unique_together)
                   if table.unique_together else ""), "",
                "| Column | Type | Required | Allowed values / format | Unit | Unique | References | "
                "Description and rule |", "|---|---|---|---|---|---|---|---|"]
        for c in table.cols:
            allowed = ", ".join(str(v) for v in c.enum) if c.enum is not None else \
                (f"`{c.pattern}`" if c.pattern else "")
            desc = c.desc + (f". Rule: {c.rule}" if c.rule else "")
            out.append(f"| {c.name} | {SQL_TYPES[c.type]} | {'yes' if c.required else 'no'} | "
                       f"{allowed.replace('|', '/')} | {c.unit} | {'yes' if c.unique else ''} | "
                       f"{c.fk or ''} | {desc.replace('|', '/')} |")
        out.append("")
    return "\n".join(out) + "\n"


# ------------------------------------------------------------------ manifest
def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def file_entries(root, rows_by_path):
    entries = []
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.name != "manifest.json":
            rel = p.relative_to(root).as_posix()
            entries.append(dict(path=rel, bytes=p.stat().st_size, rows=rows_by_path.get(rel),
                                sha256=sha256(p)))
    return entries


def export_all(out, t, gt):
    """Writes data + ground truth. Returns the row count of every data file, by path."""
    out = Path(out)
    dirs = {k: out / k for k in ("data/csv", "data/json", "data/sql", "ground_truth", "reports")}
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    rows = {}
    for table in TABLES:
        data = t[table.name]
        write_csv(dirs["data/csv"] / f"{table.name}.csv", table, data)
        write_json(dirs["data/json"] / f"{table.name}.json", table, data)
        rows[f"data/csv/{table.name}.csv"] = rows[f"data/json/{table.name}.json"] = len(data)
    write_csv(dirs["ground_truth"] / "ground_truth.csv", GROUND_TRUTH, gt)
    write_json(dirs["ground_truth"] / "ground_truth.json", GROUND_TRUTH, gt)
    rows["ground_truth/ground_truth.csv"] = rows["ground_truth/ground_truth.json"] = len(gt)
    schema_path, seed_path = dirs["data/sql"] / "schema.sql", dirs["data/sql"] / "seed.sql"
    schema_path.write_text(schema_sql(), encoding="utf-8", newline="\n")
    write_seed_sql(seed_path, t)
    (out / "DATA_DICTIONARY.md").write_text(data_dictionary_md(), encoding="utf-8", newline="\n")
    return rows


def write_json_report(path, obj):
    Path(path).write_text(json.dumps(obj, indent=2, default=jsonable) + "\n", encoding="utf-8",
                          newline="\n")
