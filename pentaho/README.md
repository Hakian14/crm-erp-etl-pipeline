# Pentaho Data Integration design

The Python pipeline in this repository is the portable, testable reference implementation.
The same flow as I built it in **Pentaho Data Integration (Kettle)** is laid out below — one job,
five transformations — so the two can be compared step for step. Each Python function maps to a
standard PDI step; no custom plugins are needed.

## Job: `JOB_crm_erp_daily.kjb`

```
START
 └─ TR_01_extract_stage_crm      (success) ─┐
 └─ TR_02_extract_stage_erp      (success) ─┤
                                            ├─► TR_03_dq_checks_quarantine
                                            └─► TR_04_resolve_customers ─► TR_05_build_marts ─► Mail: DQ summary
Any failure ─► Abort job + Mail: failure with log attachment
```

Variables: `${RAW_DIR}`, `${WAREHOUSE_JNDI}`, `${RUN_DATE}`. Logging to a `etl_run_log` table
(job entry "Set logging table") so every run is auditable.

## Transformation → step mapping

| Python (this repo) | PDI step(s) | Notes |
|---|---|---|
| `extract()` — read CSVs | **CSV file input** / **Text file input** (`;` separator for ERP, lazy conversion ON) | Set every field to String; types are assigned in staging, never at read time |
| `normalize_name` | **Replace in string** (regex, legal suffixes) → **String operations** (trim, collapse spaces, InitCap) | Keep the raw column; write to `name_clean` |
| `name_match_key` | **Replace in string** (regex `[^a-z0-9]` → "") after **String operations** (lower) | |
| `normalize_phone` | **Replace in string** (`\D` → "") → **User Defined Java Expression** for the `84`→`0` prefix and 10-digit validation | Returns null on invalid |
| `normalize_email` | **String operations** (lower, trim) → **Regex evaluation** (RFC-ish pattern) → **Filter rows** (valid ? keep : set null via **Null if**) | |
| `parse_date` (7 formats) | **Select values** → Meta-data tab, one **Date** conversion per format in a **Switch/Case** on a regex-detected format | PDI has no "try these formats" option; detect the format with **Regex evaluation** first, then convert |
| `parse_amount` | **Replace in string** (strip `VND`, `₫`, separators) → **Calculator** (× 1e6 when suffix `M`) → **Select values** (to Number) | |
| `normalize_city`, `normalize_stage` | **Stream lookup** against a small mapping table (`config/city_map.csv`, `stage_map.csv`) with "default value" on no-match | Mapping tables are maintained by the business, not in code |
| `run_checks` (expectations) | **Data Validator** step (not-null, regex, range, list) + **Merge join / Stream lookup** for referential checks → **Filter rows** → error hop to **Text file output** `quarantine_<table>.csv` | Each validation gets a named rule so `_dq_reason` can be populated via **Set field value** |
| `build_match_frame` | **Select values** (align columns) → **Append streams** (CRM + ERP) | |
| `resolve_customers` (union-find) | **Sort rows** → **Group by** on each key with **Fuzzy match** (disabled; exact keys) → iterative **Merge join** passes, or **Javascript** step implementing union-find on the sorted stream | In practice I pushed this step to SQL (`WITH RECURSIVE` on the pair table) via **Execute SQL script**: simpler to maintain than a Javascript step |
| survivorship (`pick`) | **Group by** with "first non-null" aggregation per attribute; ERP-preferred attributes via **Sort rows** on a `system_rank` field first | |
| `suggest_review_queue` | **Fuzzy match** step (Jaro-Winkler, threshold 0.90) between CRM-only and ERP-only streams, lookup on city | Output to a review sheet, never merged automatically |
| `load_sqlite` | **Table output** (truncate ON) per mart; **Execute SQL script** for views | Target was PostgreSQL in production; SQLite here for portability |
| `write_outputs` (DQ report) | **Group by** on validation results → **Text file output** (Markdown template via **Replace in string**) or **Mail** entry in the job | |

## Performance notes from the production version

- **Lazy conversion** on file inputs and **sort rows** with a large sort size (`${SORT_SIZE}` = 500k) cut staging from 9 to 3 minutes on ~2M ERP rows.
- Replaced **Database lookup** (one query per row) with **Stream lookup** (lookup table in memory) for the city/stage mappings — the single biggest win.
- Identity resolution moved from a Javascript step to a database **Execute SQL script** (`WITH RECURSIVE` over the pairs table): 10× faster and reviewable by DBAs.
- Every transformation writes row counts to `etl_run_log`; the DQ report is built from that table, not from PDI's own logging.

## Why both implementations

Pentaho is what the client ran; Python is what I can share, test and show. The rules are
identical by construction — the Python functions in `etl/transform/clean.py` are the
specification, and the PDI steps implement them.
