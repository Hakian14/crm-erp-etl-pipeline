# CRM + ERP → Customer 360: an ETL pipeline with data-quality gates

[![Python](https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&logoColor=white)](requirements.txt)
[![Pentaho](https://img.shields.io/badge/Pentaho%20PDI-design%20mapping-0096D6)](pentaho/README.md)
[![tests](https://img.shields.io/badge/tests-7%20passing-0ca30c)](tests/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

Two systems describe the same customers in two incompatible ways: the **CRM** (sales — accounts,
deals, free-text everything) and the **ERP** (finance — account codes, orders, invoices). This
pipeline turns their messy extracts into one clean analytical layer with a resolved customer
dimension, fact tables for deals, orders and invoices, a `customer_360` view, and a **data-quality
report that quarantines bad rows instead of silently loading them**.

It recreates the integration work I did on a Customer Data Platform (Pentaho in production;
Python here so it can be run and read anywhere). Data is synthetic with planted problems —
see [`data/generate_sources.py`](data/generate_sources.py) for the full list.

```
raw CSVs  ──►  staging (typed, standardised)  ──►  DQ checks ──► quarantine
                                                       │
                                                       ▼
                              identity resolution (CRM ∪ ERP → dim_customer + bridge)
                                                       │
                                                       ▼
                             marts: fact_deals · fact_orders · fact_invoices · review queue
                                                       │
                                                       ▼
                                SQLite warehouse + views (customer_360, rep reconciliation)
                                                       │
                                                       ▼
                                     outputs/dq_report.md · outputs/marts/*.csv
```

## Run it

```bash
git clone https://github.com/Hakian14/crm-erp-etl-pipeline.git
cd crm-erp-etl-pipeline
pip install -r requirements.txt          # pandas, numpy, pyyaml

python data/generate_sources.py          # (optional) regenerate the messy extracts
python run_pipeline.py                   # ~1 s: builds data/warehouse.db and outputs/
python tests/test_clean_and_match.py     # or: python -m pytest
```

Then read [`outputs/dq_report.md`](outputs/dq_report.md), or open `data/warehouse.db` and query
`customer_360`.

## What the pipeline deals with

| Problem in the sources | Where it is handled | Result on this data |
|---|---|---|
| Company names in 7 spellings (`Co., Ltd`, `Company Limited`, UPPER, double spaces) | `clean.normalize_name` → `name_match_key` | one canonical name per customer |
| Phones in 5 formats (`+84`, `(0973) 215-801`, `0973.215.801`, …) | `clean.normalize_phone` | `0XXXXXXXXX` or NULL if invalid |
| Dates in 5 formats, amounts as `12,500,000` / `12.5M` / `12500000 VND` / `12.500.000` | `clean.parse_date`, `clean.parse_amount` | typed columns; unknown formats become NULL, never a guess |
| Same company entered twice in the CRM; CRM and ERP keyed differently | `match.resolve_customers` (union-find over tax code / email / phone / name+city) | 3,382 source records → **1,969 customers**, 1,301 present in both systems |
| Customers with no shared key but a near-identical name | `match.suggest_review_queue` (fuzzy, same city) | **89 candidate pairs** for human review — never auto-merged |
| Orphan orders, duplicate invoice numbers, negative / zero / future-dated invoices | `config/expectations.yaml` → `quality.run_checks` | **172 orders and 225 invoices quarantined** with a `_dq_reason`; FK cascade keeps marts consistent |
| City spellings (`HCMC`, `Sài Gòn`, `TP.HCM`), stage typos (`Propsal`, `Closed-Won`) | mapping tables in `clean.py` | canonical values; unknown stages fail a critical check |

Identity resolution is scored against the synthetic truth key (which the pipeline never reads):
**pairwise precision 1.000, recall 0.972** — the 2.8% missed are the planted "hard cases" with no
email, no phone and a misspelled name, and 100% of them appear in the review queue.

## Data-quality philosophy

Checks are **declared, not coded** ([`config/expectations.yaml`](config/expectations.yaml)):

```yaml
stg_erp_invoices:
  - {column: invoice_no, check: unique,       severity: critical, note: "duplicate invoice numbers in extract"}
  - {column: amount,     check: range, min: 1, severity: critical, note: "zero or negative invoice"}
  - {column: invoice_date, check: not_future, severity: critical}
  - {column: order_no,   check: foreign_key, ref_table: stg_erp_orders, ref_column: order_no, severity: critical}
```

- **critical** → failing rows are quarantined (`outputs/quarantine/`, `quarantine_*` tables) and excluded from marts; the run still completes so good data is not held hostage by bad rows.
- **warning** → rows are kept, the count is reported.
- Checks run twice: on staging (source problems) and on marts (pipeline problems). A mart failure is a bug; a staging failure is a conversation with the source-system owner.

Report excerpt from this run:

| Status | Table | Column | Check | Failed | Note |
|---|---|---|---|---|---|
| ❌ FAIL | stg_erp_orders | account_code | foreign_key | 172 (2.9%) | orphan order — account not in ERP extract |
| ❌ FAIL | stg_erp_invoices | invoice_no | unique | 40 (0.7%) | duplicate invoice numbers in extract |
| ❌ FAIL | stg_erp_invoices | amount | range | 25 (0.5%) | zero or negative invoice |
| ⚠️ WARN | stg_crm_accounts | email_clean | not_null | 194 (10.6%) | invalid or blank email in CRM |
| ✅ PASS | fact_orders | customer_key | foreign_key | 0 | |

## What you can ask the warehouse

```sql
-- Receivables risk by customer status
SELECT customer_status, COUNT(*) AS customers, SUM(overdue_vnd) / 1e9 AS overdue_bn
FROM customer_360 GROUP BY 1 ORDER BY 2 DESC;

-- Reps whose CRM "won" deals do not turn into delivered orders
SELECT * FROM sales_rep_reconciliation;

-- Customers the CRM knows but finance has never invoiced
SELECT customer_name, city, deals_won, won_amount_vnd
FROM customer_360 WHERE in_crm = 1 AND in_erp = 0 AND deals_won > 0;
```

## Pentaho

The production version of this flow ran in **Pentaho Data Integration**. [`pentaho/README.md`](pentaho/README.md)
maps every Python function to the PDI steps that implement it (job layout, transformations,
Data Validator rules, Stream lookup mappings, Execute SQL for identity resolution) and lists the
performance changes that took the daily run from 9 to 3 minutes.

## Repository layout

```
├── data/
│   ├── generate_sources.py       # messy CRM/ERP extracts with planted problems (seeded)
│   └── raw/crm, raw/erp          # the extracts; _evaluation_key.csv is for scoring only
├── etl/
│   ├── pipeline.py               # orchestration: extract → stage → DQ → resolve → marts → load → report
│   ├── quality.py                # declarative checks + quarantine
│   └── transform/
│       ├── clean.py              # pure standardisation functions (unit-tested)
│       └── match.py              # identity resolution (union-find), survivorship, review queue
├── config/expectations.yaml      # data-quality rules
├── sql/marts/                    # customer_360, sales_rep_reconciliation views
├── pentaho/README.md             # PDI design mapping
├── docs/data_dictionary.md
├── outputs/                      # dq_report.md, marts/*.csv, quarantine/*.csv, pipeline_run.log
├── tests/
└── run_pipeline.py
```

## About

Built by **Ha Kieu Anh** — Data Analyst (5+ years in e-commerce, retail and CRM analytics; Pentaho ETL
and data-quality work on a Customer Data Platform), now also working in AI training & evaluation.
[GitHub](https://github.com/Hakian14) · [LinkedIn](https://www.linkedin.com/in/kieu-anh-ha-6249a3172/) · hakieuanh662@gmail.com

License: [MIT](LICENSE)
