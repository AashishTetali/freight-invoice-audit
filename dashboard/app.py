"""Dashboard.   Run:   streamlit run dashboard/app.py

Reads the files produced by `python -m freight_audit run ...` (nothing is recomputed).
Charts are fixed (no zoom or drag) and show a small pop-up when you hover a bar."""
import json
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

OUTPUT = Path(__file__).resolve().parent.parent / "output"
st.set_page_config(page_title="Freight invoice audit", layout="wide")
# Charts are fixed pictures: hide the toolbar (full-screen / zoom buttons) on charts only.
st.markdown("""<style>
[data-testid="stElementContainer"]:has([data-testid="stVegaLiteChart"]) [data-testid="stElementToolbar"] {display: none;}
</style>""", unsafe_allow_html=True)

scales = sorted(p.name for p in OUTPUT.iterdir() if (p / "manifest.json").exists()) \
    if OUTPUT.exists() else []
if not scales:
    st.error("No results yet. In a terminal run:  python -m freight_audit run --scale starter")
    st.stop()
scale = st.sidebar.selectbox("Dataset", scales, index=scales.index("full") if "full" in scales else 0)
st.sidebar.caption("starter = 10 carriers / 1,000 shipments / 750 invoices\n\n"
                   "full = 100 carriers / 20,000 shipments / 15,000 invoices")


@st.cache_data
def load(scale):
    base = OUTPUT / scale
    numeric = {"freight_invoice": ["total_billed"],
               "invoice_line": ["amount", "quantity", "unit_rate"],
               "invoice_audit": ["billed_total", "expected_total", "variance_total"],
               "audit_exception": ["expected_amount", "billed_amount", "variance_amount", "approved_amount"],
               "credit_note": ["credit_amount"],
               "invoice_status_history": [], "exception_status_history": []}
    data = {}
    for name, cols in numeric.items():
        df = pd.read_csv(base / "data" / "csv" / f"{name}.csv", dtype=str, keep_default_na=False)
        for c in cols:
            df[c] = pd.to_numeric(df[c])
        data[name] = df
    reports = {}
    for f in ("audit_metrics", "validation_summary", "reproducibility"):
        p = base / "reports" / f"{f}.json"
        reports[f] = json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    return data, reports


def bar(values, label, value_title, fmt=",.0f", color="#2a78d6"):
    """Horizontal bar chart: fixed size (no zoom, no drag), small pop-up on hover."""
    df = pd.DataFrame({"item": list(values.index), "value": list(values.values)})
    chart = alt.Chart(df).mark_bar(color=color, cornerRadiusEnd=4, size=16).encode(
        x=alt.X("value:Q", title=value_title),
        y=alt.Y("item:N", title=None, sort="-x"),
        tooltip=[alt.Tooltip("item:N", title=label), alt.Tooltip("value:Q", title=value_title, format=fmt)],
    ).properties(height=max(120, 28 * len(df)))
    st.altair_chart(chart, width="stretch")


data, reports = load(scale)
m = reports["audit_metrics"]
b, det = m["business"], m["detection"]
st.title("Freight invoice audit")
st.caption("Every carrier bill is re-priced from the contract and the delivery record. "
           "Differences become exceptions, disputes and credit notes.")

tabs = st.tabs(["Overview", "Exceptions", "Invoice explorer", "Carriers",
                "Detection scorecard", "Data quality"])

with tabs[0]:
    c = st.columns(6)
    c[0].metric("Invoices audited", f"{b['invoices_audited']:,}")
    c[1].metric("Correct bills", f"{b['clean_rate']:.1%}")
    c[2].metric("Total billed", f"${b['total_billed']:,.0f}")
    c[3].metric("Disputed", f"${b['disputed_amount']:,.0f}")
    c[4].metric("Credits applied", f"${b['credits_applied']:,.0f}")
    c[5].metric("Money saved", f"${b['savings_total']:,.0f}",
                f"{b['savings_pct_of_billed']:.1%} of billed")
    by_type = pd.DataFrame(b["exceptions_by_type"]).T
    left, right = st.columns(2)
    with left:
        st.subheader("Exceptions by type")
        bar(by_type["count"], "Exception type", "Exceptions")
        st.subheader("Invoice status")
        bar(pd.Series(b["invoice_status"]), "Status", "Invoices")
    with right:
        st.subheader("$ variance by type")
        bar(by_type["variance"], "Exception type", "Variance ($)", fmt="$,.2f")
        st.subheader("Exception status")
        bar(pd.Series(b["exception_status"]), "Status", "Exceptions")

with tabs[1]:
    ex = data["audit_exception"]
    f1, f2 = st.columns(2)
    types = f1.multiselect("Type", sorted(ex.exception_type.unique()))
    status = f2.multiselect("Status", sorted(ex.current_status.unique()))
    view = ex
    if types:
        view = view[view.exception_type.isin(types)]
    if status:
        view = view[view.current_status.isin(status)]
    st.write(f"{len(view):,} exceptions, total variance ${view.variance_amount.sum():,.2f}")
    st.dataframe(view[["exception_id", "invoice_id", "line_id", "exception_type", "expected_amount",
                       "billed_amount", "variance_amount", "current_status", "detail"]],
                 hide_index=True, width="stretch")
    st.download_button("Download as CSV", view.to_csv(index=False), "exceptions.csv")

with tabs[2]:
    flagged = sorted(data["audit_exception"].invoice_id.unique())
    choice = st.selectbox("Invoice (those with exceptions are listed first)",
                          flagged + [i for i in data["freight_invoice"].invoice_id if i not in set(flagged)])
    inv = data["freight_invoice"].set_index("invoice_id").loc[choice]
    aud = data["invoice_audit"].set_index("invoice_id").loc[choice]
    c = st.columns(4)
    c[0].metric("Billed", f"${aud.billed_total:,.2f}")
    c[1].metric("Expected", f"${aud.expected_total:,.2f}")
    c[2].metric("Difference", f"${aud.variance_total:,.2f}")
    c[3].metric("Status", inv.current_status)
    st.write(f"Carrier **{inv.carrier_id}** | shipment ref **{inv.shipment_ref}** | rate card "
             f"**{inv.rate_card_id}** (expected **{aud.expected_rate_card_id or '-'}**) | billed "
             f"weight {inv.billed_weight_lb} lb (expected {aud.expected_billable_weight_lb or '-'} lb)")
    lines = data["invoice_line"][data["invoice_line"].invoice_id == choice]
    exs = data["audit_exception"][data["audit_exception"].invoice_id == choice]
    st.subheader("Invoice lines and the audit verdict")
    merged = lines.merge(exs[["line_id", "exception_id", "exception_type", "variance_amount"]],
                         on="line_id", how="left").fillna("").astype(str)
    st.dataframe(merged[["line_no", "charge_type", "accessorial_code", "quantity", "unit",
                         "unit_rate", "amount", "exception_id", "exception_type",
                         "variance_amount"]], hide_index=True, width="stretch")
    if len(exs):
        st.subheader("Exceptions")
        for _, e in exs.iterrows():
            st.markdown(f"* **{e.exception_id} {e.exception_type}** ({e.current_status}): {e.detail}")
    cn = data["credit_note"][data["credit_note"].invoice_id == choice]
    if len(cn):
        st.subheader("Credit notes")
        st.dataframe(cn, hide_index=True, width="stretch")
    st.subheader("History (nothing is overwritten)")
    ih = data["invoice_status_history"]
    eh = data["exception_status_history"]
    hist = pd.concat([ih[ih.invoice_id == choice].assign(record=choice),
                      eh[eh.exception_id.isin(exs.exception_id)].rename(
                          columns={"exception_id": "record"})])
    st.dataframe(hist[["event_date", "record", "from_status", "to_status", "actor_role", "note"]]
                 .sort_values(["event_date", "record"]), hide_index=True, width="stretch")

with tabs[3]:
    cars = pd.DataFrame(m["carriers_by_exception_rate"])
    st.subheader("Carriers ranked by share of invoices with problems")
    bar(cars.set_index("carrier_name")["exception_rate"].head(20), "Carrier", "Share of invoices with problems",
        fmt=".1%")
    st.dataframe(cars, hide_index=True, width="stretch")

with tabs[4]:
    o = det["overall"]
    c = st.columns(4)
    c[0].metric("Precision", f"{o['precision']:.1%}")
    c[1].metric("Recall", f"{o['recall']:.1%}")
    c[2].metric("False alarms on clean bills", f"{det['clean_invoices_falsely_flagged']}")
    c[3].metric("Decisions matching rules", f"{det['decision_accuracy']:.1%}")
    st.caption("The answer key (ground_truth) is used ONLY here, to score the audit engine. "
               "The engine itself never reads it.")
    st.dataframe(pd.DataFrame(det["per_issue_type"]).T[
        ["planted", "detected", "true_positive", "false_positive", "false_negative",
         "precision", "recall", "f1"]], width="stretch")

with tabs[5]:
    v = reports["validation_summary"]
    st.metric("Validation", v["overall"], f"{v['checks_passed']} of {v['checks_run']} checks passed")
    st.dataframe(pd.DataFrame(v["checks"])[["status", "category", "name", "checked", "failed"]],
                 hide_index=True, width="stretch")
    r = reports["reproducibility"]
    if r:
        st.metric("Reproducibility", "IDENTICAL" if r["all_identical"] else "DIFFERENT",
                  f"{r['files_compared']} files compared across two runs with seed {r['seed']}")
    else:
        st.info("Run  python -m freight_audit reproduce --scale " + scale)
