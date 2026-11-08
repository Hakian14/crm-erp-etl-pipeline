-- ---------------------------------------------------------------------------
-- sales_rep_reconciliation: CRM "won" pipeline vs ERP delivered revenue per rep.
-- The classic CRM-vs-finance reconciliation question: are reps closing deals
-- that never turn into orders, or booking orders that were never in the CRM?
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS sales_rep_reconciliation;
CREATE VIEW sales_rep_reconciliation AS
WITH crm AS (
    SELECT owner AS sales_rep, COUNT(*) AS deals_won, SUM(amount_vnd) AS won_amount_vnd
    FROM fact_deals WHERE is_won = 1 GROUP BY owner
),
erp AS (
    SELECT sales_rep, COUNT(*) AS orders_delivered, SUM(net_amount_vnd) AS delivered_vnd
    FROM fact_orders WHERE status = 'DELIVERED' GROUP BY sales_rep
)
SELECT COALESCE(crm.sales_rep, erp.sales_rep) AS sales_rep,
       COALESCE(deals_won, 0)        AS deals_won,
       won_amount_vnd,
       COALESCE(orders_delivered, 0) AS orders_delivered,
       delivered_vnd,
       ROUND(1.0 * delivered_vnd / NULLIF(won_amount_vnd, 0), 2) AS delivered_to_won_ratio
FROM crm FULL OUTER JOIN erp ON crm.sales_rep = erp.sales_rep
ORDER BY delivered_vnd DESC;
