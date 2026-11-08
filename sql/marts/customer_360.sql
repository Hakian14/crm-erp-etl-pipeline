-- ---------------------------------------------------------------------------
-- customer_360: one row per resolved customer with pipeline, revenue and
-- receivables metrics from both systems. Built as a view over the marts so it
-- is always consistent with the latest load.
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS customer_360;
CREATE VIEW customer_360 AS
WITH deals AS (
    SELECT customer_key,
           COUNT(*)                                            AS deals_total,
           SUM(is_won)                                         AS deals_won,
           SUM(CASE WHEN is_won = 1 THEN amount_vnd END)       AS won_amount_vnd,
           SUM(CASE WHEN stage IN ('Proposal','Negotiation') THEN amount_vnd END) AS open_pipeline_vnd,
           MAX(CASE WHEN is_won = 1 THEN close_date END)       AS last_won_date
    FROM fact_deals GROUP BY customer_key
),
orders AS (
    SELECT customer_key,
           COUNT(*)                                            AS orders_total,
           SUM(CASE WHEN status = 'DELIVERED' THEN net_amount_vnd END) AS delivered_revenue_vnd,
           MIN(order_date)                                     AS first_order_date,
           MAX(order_date)                                     AS last_order_date
    FROM fact_orders GROUP BY customer_key
),
invoices AS (
    SELECT customer_key,
           SUM(amount_vnd)                                     AS invoiced_vnd,
           SUM(CASE WHEN is_paid = 1 THEN amount_vnd END)      AS paid_vnd,
           SUM(CASE WHEN is_paid = 0 THEN amount_vnd END)      AS outstanding_vnd,
           SUM(CASE WHEN is_paid = 0 AND days_overdue > 0 THEN amount_vnd END) AS overdue_vnd,
           ROUND(AVG(CASE WHEN is_paid = 1 THEN days_to_pay END), 1) AS avg_days_to_pay
    FROM fact_invoices GROUP BY customer_key
)
SELECT c.customer_key, c.customer_name, c.city, c.industry, c.tax_code, c.email, c.phone,
       c.in_crm, c.in_erp, c.n_source_records,
       COALESCE(d.deals_total, 0)        AS deals_total,
       COALESCE(d.deals_won, 0)          AS deals_won,
       d.won_amount_vnd, d.open_pipeline_vnd, d.last_won_date,
       COALESCE(o.orders_total, 0)       AS orders_total,
       o.delivered_revenue_vnd, o.first_order_date, o.last_order_date,
       i.invoiced_vnd, i.paid_vnd, i.outstanding_vnd, i.overdue_vnd, i.avg_days_to_pay,
       CASE
           WHEN i.overdue_vnd > 0 THEN 'Overdue'
           WHEN o.last_order_date >= DATE((SELECT MAX(order_date) FROM fact_orders), '-90 days') THEN 'Active'
           WHEN o.orders_total IS NULL AND d.deals_total > 0 THEN 'Prospect'
           ELSE 'Dormant'
       END AS customer_status
FROM dim_customer c
LEFT JOIN deals    d USING (customer_key)
LEFT JOIN orders   o USING (customer_key)
LEFT JOIN invoices i USING (customer_key);
