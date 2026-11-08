# Data-quality report

Run: 2026-11-08 16:13  ·  checks: 35  ·  FAIL 5  ·  WARN 4

## Row counts through the pipeline

| Stage | Table | Rows |
|---|---|---|
| raw | crm_accounts | 1,823 |
| raw | crm_deals | 4,000 |
| raw | erp_accounts | 1,559 |
| raw | erp_orders | 6,000 |
| raw | erp_invoices | 5,584 |
| quarantined | stg_erp_orders | 172 |
| quarantined | stg_erp_invoices | 225 |
| mart | dim_customer | 1,969 |
| mart | bridge_customer_source | 3,382 |
| mart | fact_deals | 4,000 |
| mart | fact_orders | 5,828 |
| mart | fact_invoices | 5,359 |
| mart | review_queue_possible_duplicates | 89 |

## Customer identity resolution

- Source records: 3,382 (CRM + ERP) → **1,969 unique customers**
- Present in both systems: 1,301; CRM only: 410; ERP only: 258
- Match keys used: name_city: 2,733, phone: 2,119, email: 1,846
- Manual review queue (CRM-only vs ERP-only, same city, name similarity ≥ 0.90): **89 candidate pairs** in `outputs/marts/review_queue_possible_duplicates.csv` — suggestions only, never auto-merged
- Accuracy against the synthetic truth key (evaluation only): pairwise precision **1.000**, recall **0.972**, F1 0.986; 1,969 resolved vs 1,930 true

## Staging checks

| Status | Table | Column | Check | Severity | Failed | % | Note |
|---|---|---|---|---|---|---|---|
| ❌ FAIL | stg_erp_invoices | invoice_no | unique | critical | 40 | 0.7 | duplicate invoice numbers in extract |
| ❌ FAIL | stg_erp_invoices | amount | range | critical | 25 | 0.5 | zero or negative invoice |
| ❌ FAIL | stg_erp_invoices | invoice_date | not_future | critical | 10 | 0.2 |  |
| ❌ FAIL | stg_erp_invoices | order_no | foreign_key | critical | 152 | 2.7 |  |
| ❌ FAIL | stg_erp_orders | account_code | foreign_key | critical | 172 | 2.9 | orphan order - account not in ERP extract |
| ⚠️ WARN | stg_crm_accounts | email_clean | not_null | warning | 194 | 10.6 | invalid or blank email in CRM |
| ⚠️ WARN | stg_crm_accounts | phone_clean | not_null | warning | 159 | 8.7 | phone missing or not a valid VN number |
| ⚠️ WARN | stg_crm_deals | amount_vnd | not_null | warning | 95 | 2.4 | amount blank/TBD |
| ⚠️ WARN | stg_crm_deals | close_date_clean | not_null | warning | 150 | 3.8 |  |
| ✅ PASS | stg_crm_accounts | crm_id | not_null | critical | 0 | 0.0 |  |
| ✅ PASS | stg_crm_accounts | crm_id | unique | critical | 0 | 0.0 |  |
| ✅ PASS | stg_crm_accounts | name_clean | not_null | critical | 0 | 0.0 |  |
| ✅ PASS | stg_crm_accounts | created_at_clean | not_null | warning | 0 | 0.0 | unparseable created_at |
| ✅ PASS | stg_crm_accounts | status_clean | in_set | warning | 0 | 0.0 |  |
| ✅ PASS | stg_crm_deals | deal_id | unique | critical | 0 | 0.0 |  |
| ✅ PASS | stg_crm_deals | crm_id | foreign_key | critical | 0 | 0.0 |  |
| ✅ PASS | stg_crm_deals | stage_clean | not_null | critical | 0 | 0.0 | stage not in the known list |
| ✅ PASS | stg_crm_deals | amount_vnd | range | warning | 0 | 0.0 |  |
| ✅ PASS | stg_erp_accounts | account_code | unique | critical | 0 | 0.0 |  |
| ✅ PASS | stg_erp_accounts | tax_code_clean | regex | warning | 0 | 0.0 | tax code not 10 digits |
| ✅ PASS | stg_erp_invoices | due_date | not_null | warning | 0 | 0.0 |  |
| ✅ PASS | stg_erp_orders | order_no | unique | critical | 0 | 0.0 |  |
| ✅ PASS | stg_erp_orders | net_amount | range | critical | 0 | 0.0 |  |
| ✅ PASS | stg_erp_orders | status_clean | in_set | critical | 0 | 0.0 |  |
| ✅ PASS | stg_erp_orders | currency_clean | in_set | warning | 0 | 0.0 |  |

## Mart checks

| Status | Table | Column | Check | Severity | Failed | % | Note |
|---|---|---|---|---|---|---|---|
| ✅ PASS | dim_customer | customer_key | unique | critical | 0 | 0.0 |  |
| ✅ PASS | dim_customer | customer_name | not_null | critical | 0 | 0.0 |  |
| ✅ PASS | dim_customer | city | not_null | warning | 0 | 0.0 |  |
| ✅ PASS | fact_deals | deal_id | unique | critical | 0 | 0.0 |  |
| ✅ PASS | fact_deals | customer_key | foreign_key | critical | 0 | 0.0 |  |
| ✅ PASS | fact_invoices | invoice_no | unique | critical | 0 | 0.0 |  |
| ✅ PASS | fact_invoices | customer_key | foreign_key | critical | 0 | 0.0 |  |
| ✅ PASS | fact_orders | order_no | unique | critical | 0 | 0.0 |  |
| ✅ PASS | fact_orders | customer_key | foreign_key | critical | 0 | 0.0 |  |
| ✅ PASS | fact_orders | net_amount_vnd | range | critical | 0 | 0.0 |  |

Quarantined rows are in `outputs/quarantine/` with a `_dq_reason` column; they are excluded from the marts but kept for the source-system owners to fix.
