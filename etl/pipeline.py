"""
Pipeline orchestration: extract -> stage (clean) -> resolve customers -> DQ -> marts -> load -> report.

    python run_pipeline.py                 # full run, writes data/warehouse.db and outputs/
    python run_pipeline.py --no-db         # skip SQLite load (CSV marts only)

Idempotent: every run rebuilds staging and marts from the raw extracts.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from datetime import datetime

import pandas as pd
import yaml

from .quality import results_frame, run_checks
from .transform import clean as C
from .transform.match import build_match_frame, resolve_customers, suggest_review_queue

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw")
OUT = os.path.join(ROOT, "outputs")
DB = os.path.join(ROOT, "data", "warehouse.db")
SQL_MARTS = os.path.join(ROOT, "sql", "marts")

log = logging.getLogger("etl")


# ------------------------------------------------------------------------------ extract

def extract() -> dict[str, pd.DataFrame]:
    raw = {
        "crm_accounts": pd.read_csv(os.path.join(RAW, "crm", "accounts.csv"), dtype=str, keep_default_na=False),
        "crm_deals": pd.read_csv(os.path.join(RAW, "crm", "deals.csv"), dtype=str, keep_default_na=False),
        "erp_accounts": pd.read_csv(os.path.join(RAW, "erp", "accounts.csv"), sep=";", dtype=str, keep_default_na=False),
        "erp_orders": pd.read_csv(os.path.join(RAW, "erp", "sales_orders.csv"), sep=";", dtype=str, keep_default_na=False),
        "erp_invoices": pd.read_csv(os.path.join(RAW, "erp", "invoices.csv"), sep=";", dtype=str, keep_default_na=False),
    }
    for name, df in raw.items():
        df.replace({"": None}, inplace=True)
        log.info("extracted %-14s %6d rows", name, len(df))
    return raw


# ------------------------------------------------------------------------------ staging

def stage(raw: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    a = raw["crm_accounts"].copy()
    a["name_clean"] = a["account_name"].map(C.normalize_name)
    a["name_key"] = a["name_clean"].map(C.name_match_key)
    a["email_clean"] = a["email"].map(C.normalize_email)
    a["phone_clean"] = a["phone"].map(C.normalize_phone)
    a["city_clean"] = a["city"].map(C.normalize_city)
    a["industry_clean"] = a["industry"].map(lambda v: C.normalize_name(v) if v else None).replace({"E&B": "F&B", "F&B": "F&B"})
    a["created_at_clean"] = a["created_at"].map(C.parse_date)
    a["status_clean"] = a["status"].map(lambda v: C.normalize_upper(v, {"ACTIVE", "INACTIVE", "PROSPECT"}, default="UNKNOWN"))

    d = raw["crm_deals"].copy()
    d["stage_clean"] = d["stage"].map(C.normalize_stage)
    d["amount_vnd"] = d["amount"].map(C.parse_amount)
    d["close_date_clean"] = d["close_date"].map(C.parse_date)
    d["is_won"] = d["stage_clean"].eq("Closed Won")

    e = raw["erp_accounts"].copy()
    e["name_clean"] = e["account_name"].map(C.normalize_name)
    e["name_key"] = e["name_clean"].map(C.name_match_key)
    e["email_clean"] = e["email"].map(C.normalize_email)
    e["phone_clean"] = e["phone"].map(C.normalize_phone)
    e["city_clean"] = e["city"].map(C.normalize_city)
    e["tax_code_clean"] = e["tax_code"].map(lambda v: v.strip() if v else None)
    e["payment_terms_clean"] = e["payment_terms"].map(lambda v: C.normalize_upper(v, {"NET15", "NET30", "NET45", "COD"}, default="UNKNOWN"))
    e["credit_limit_vnd"] = pd.to_numeric(e["credit_limit"], errors="coerce")

    o = raw["erp_orders"].copy()
    o["order_date"] = o["order_date"].map(C.parse_date)
    o["status_clean"] = o["status"].map(lambda v: C.normalize_upper(v, {"DELIVERED", "SHIPPED", "CANCELLED", "OPEN"}))
    o["currency_clean"] = o["currency"].map(C.normalize_currency)
    o["net_amount"] = pd.to_numeric(o["net_amount"], errors="coerce")
    o["sales_rep_clean"] = o["sales_rep"].replace({"L. Tran": "Linh Tran"})

    i = raw["erp_invoices"].copy()
    for col in ("invoice_date", "due_date", "paid_date"):
        i[col] = i[col].map(C.parse_date)
    i["amount"] = pd.to_numeric(i["amount"], errors="coerce")
    i["vat_rate"] = pd.to_numeric(i["vat_rate"], errors="coerce")

    return {"stg_crm_accounts": a, "stg_crm_deals": d, "stg_erp_accounts": e, "stg_erp_orders": o, "stg_erp_invoices": i}


# ------------------------------------------------------------------------------ marts

def build_marts(stg: dict[str, pd.DataFrame]) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    mf = build_match_frame(stg["stg_crm_accounts"], stg["stg_erp_accounts"])
    dim_customer, bridge = resolve_customers(mf)
    crm_map = bridge[bridge["system"] == "crm"].set_index("source_id")["customer_key"]
    erp_map = bridge[bridge["system"] == "erp"].set_index("source_id")["customer_key"]

    d = stg["stg_crm_deals"]
    fact_deals = pd.DataFrame({
        "deal_id": d["deal_id"], "customer_key": d["crm_id"].map(crm_map), "crm_id": d["crm_id"],
        "deal_name": d["deal_name"], "stage": d["stage_clean"], "is_won": d["is_won"].astype(int),
        "amount_vnd": d["amount_vnd"], "close_date": d["close_date_clean"], "owner": d["owner"],
    })
    o = stg["stg_erp_orders"]
    fact_orders = pd.DataFrame({
        "order_no": o["order_no"], "customer_key": o["account_code"].map(erp_map), "account_code": o["account_code"],
        "order_date": o["order_date"], "status": o["status_clean"], "net_amount_vnd": o["net_amount"],
        "sales_rep": o["sales_rep_clean"],
    })
    i = stg["stg_erp_invoices"]
    as_of = i["invoice_date"].max()          # snapshot date = latest invoice, so results are reproducible
    fact_invoices = pd.DataFrame({
        "invoice_no": i["invoice_no"], "order_no": i["order_no"], "customer_key": i["account_code"].map(erp_map),
        "invoice_date": i["invoice_date"], "due_date": i["due_date"], "paid_date": i["paid_date"],
        "amount_vnd": i["amount"], "vat_rate": i["vat_rate"],
        "is_paid": i["paid_date"].notna().astype(int),
        "days_to_pay": (i["paid_date"] - i["invoice_date"]).dt.days,
        "days_overdue": ((i["paid_date"].fillna(as_of) - i["due_date"]).dt.days).clip(lower=0),
    })
    review_queue = suggest_review_queue(dim_customer)
    return {"dim_customer": dim_customer, "bridge_customer_source": bridge, "fact_deals": fact_deals,
            "fact_orders": fact_orders, "fact_invoices": fact_invoices, "review_queue_possible_duplicates": review_queue}, mf


# ------------------------------------------------------------------------------ load

def load_sqlite(stg: dict, marts: dict, quarantined: dict) -> None:
    if os.path.exists(DB):
        os.remove(DB)
    con = sqlite3.connect(DB)
    for name, df in {**stg, **marts}.items():
        out = df.copy()
        for col in out.columns:
            if pd.api.types.is_datetime64_any_dtype(out[col]):
                out[col] = out[col].dt.strftime("%Y-%m-%d")
        out.to_sql(name, con, index=False, if_exists="replace")
    for name, df in quarantined.items():
        df.to_sql(f"quarantine_{name}", con, index=False, if_exists="replace")
    for f in sorted(os.listdir(SQL_MARTS)):
        if f.endswith(".sql"):
            with open(os.path.join(SQL_MARTS, f), encoding="utf-8") as fh:
                con.executescript(fh.read())
            log.info("applied %s", f)
    con.commit()
    con.close()


def write_outputs(stg_results, mart_results, quarantined, marts, raw_counts, match_eval, started) -> None:
    os.makedirs(os.path.join(OUT, "quarantine"), exist_ok=True)
    os.makedirs(os.path.join(OUT, "marts"), exist_ok=True)
    for name, df in quarantined.items():
        df.to_csv(os.path.join(OUT, "quarantine", f"{name}.csv"), index=False)
    for name, df in marts.items():
        df.to_csv(os.path.join(OUT, "marts", f"{name}.csv"), index=False)

    stg_df, mart_df = results_frame(stg_results), results_frame(mart_results)
    lines = [f"# Data-quality report", "", f"Run: {started:%Y-%m-%d %H:%M}  ·  checks: {len(stg_df) + len(mart_df)}  ·  "
             f"FAIL {int((stg_df.status == 'FAIL').sum() + (mart_df.status == 'FAIL').sum())}  ·  "
             f"WARN {int((stg_df.status == 'WARN').sum() + (mart_df.status == 'WARN').sum())}", "",
             "## Row counts through the pipeline", "", "| Stage | Table | Rows |", "|---|---|---|"]
    for t, n in raw_counts.items():
        lines.append(f"| raw | {t} | {n:,} |")
    for t, q in quarantined.items():
        lines.append(f"| quarantined | {t} | {len(q):,} |")
    for t, df in marts.items():
        lines.append(f"| mart | {t} | {len(df):,} |")
    lines += ["", "## Customer identity resolution", "",
              f"- Source records: {marts['bridge_customer_source'].shape[0]:,} (CRM + ERP) → **{len(marts['dim_customer']):,} unique customers**",
              f"- Present in both systems: {int(marts['dim_customer'][['in_crm', 'in_erp']].all(axis=1).sum()):,}; CRM only: "
              f"{int((marts['dim_customer']['in_crm'] & ~marts['dim_customer']['in_erp']).sum()):,}; ERP only: "
              f"{int((~marts['dim_customer']['in_crm'] & marts['dim_customer']['in_erp']).sum()):,}",
              f"- Match keys used: " + ", ".join(f"{k}: {v:,}" for k, v in marts['bridge_customer_source']['matched_on'].dropna().str.split(',').explode().value_counts().items())]
    rq = marts["review_queue_possible_duplicates"]
    lines += [f"- Manual review queue (CRM-only vs ERP-only, same city, name similarity ≥ 0.90): **{len(rq):,} candidate pairs** "
              f"in `outputs/marts/review_queue_possible_duplicates.csv` — suggestions only, never auto-merged"]
    if match_eval:
        lines += [f"- Accuracy against the synthetic truth key (evaluation only): pairwise precision **{match_eval['precision']:.3f}**, "
                  f"recall **{match_eval['recall']:.3f}**, F1 {match_eval['f1']:.3f}; {match_eval['customers_resolved']:,} resolved vs {match_eval['customers_truth']:,} true"]
    for title, df in (("Staging checks", stg_df), ("Mart checks", mart_df)):
        lines += ["", f"## {title}", "", "| Status | Table | Column | Check | Severity | Failed | % | Note |", "|---|---|---|---|---|---|---|---|"]
        for _, r in df.sort_values(["status", "table"], key=lambda s: s.map({"FAIL": 0, "WARN": 1, "PASS": 2}) if s.name == "status" else s).iterrows():
            icon = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌"}[r.status]
            lines.append(f"| {icon} {r.status} | {r.table} | {r.column} | {r.check} | {r.severity} | {r.rows_failed:,} | {r.fail_pct:.1f} | {r.note} |")
    lines += ["", "Quarantined rows are in `outputs/quarantine/` with a `_dq_reason` column; they are excluded from the marts "
              "but kept for the source-system owners to fix.", ""]
    with open(os.path.join(OUT, "dq_report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ------------------------------------------------------------------------------ run

def run(load_db: bool = True) -> dict:
    started = datetime.now()
    t0 = time.time()
    with open(os.path.join(ROOT, "config", "expectations.yaml"), encoding="utf-8") as f:
        expectations = yaml.safe_load(f)

    raw = extract()
    raw_counts = {k: len(v) for k, v in raw.items()}
    stg = stage(raw)
    log.info("staging complete")
    stg_results, quarantined = run_checks(stg, expectations["staging"])
    for t, q in quarantined.items():
        log.warning("quarantined %4d rows from %s", len(q), t)
    marts, _ = build_marts(stg)
    mart_results, mart_quarantine = run_checks(marts, expectations["marts"])
    quarantined.update({k: v for k, v in mart_quarantine.items()})
    log.info("marts built: %s", ", ".join(f"{k}={len(v):,}" for k, v in marts.items()))

    match_eval = None
    key_path = os.path.join(RAW, "_evaluation_key.csv")
    if os.path.exists(key_path):
        from .transform.match import evaluate_matching
        match_eval = evaluate_matching(marts["bridge_customer_source"], pd.read_csv(key_path, dtype={"source_id": str}))
        log.info("matching vs truth key: precision %.3f recall %.3f", match_eval["precision"], match_eval["recall"])

    if load_db:
        load_sqlite(stg, marts, quarantined)
        log.info("loaded %s", os.path.relpath(DB, ROOT))
    write_outputs(stg_results, mart_results, quarantined, marts, raw_counts, match_eval, started)
    log.info("done in %.1fs -> outputs/dq_report.md", time.time() - t0)
    return {"marts": marts, "stg_results": stg_results, "mart_results": mart_results, "quarantined": quarantined, "match_eval": match_eval}
