-- Models (requirement 2). Executed by `orders-load` after the table is loaded.
-- Keep the view names in sync with ALLOWED_TABLES in orders_nl/config.py.

-- TODO: v_sales_by_month_country
--   One row per (month, country): month (date, first day), country,
--   orders count, total amount. Decide whether cancelled/refunded orders count.
-- CREATE OR REPLACE VIEW analytics.v_sales_by_month_country AS
-- SELECT ...;

-- TODO: v_top_customers
--   Top 10 customers by total amount: customer, orders count, total amount, rank.
--   Decide tie-breaking and which statuses count as a sale (same rule as above).
-- CREATE OR REPLACE VIEW analytics.v_top_customers AS
-- SELECT ...;

-- Sentinel so the file is never an empty statement.
SELECT 1;
