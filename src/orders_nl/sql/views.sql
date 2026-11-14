-- Models (requirement 2). Executed by `orders-load` after the table is loaded.
-- View names must match ALLOWED_TABLES in orders_nl/config.py.
--
-- Business rule shared by both views: only `completed` orders count as sales
-- (see orders_nl.domain.SALE_STATUSES).
--
-- DROP + CREATE (not CREATE OR REPLACE) so column changes never fail; the
-- read-only role regains SELECT through the schema's default privileges.

-- Sales per calendar month and country.
-- year/month are plain integers so questions like "en marzo" map to `month = 3`
-- without date arithmetic (easier for a small LLM to get right).
DROP VIEW IF EXISTS analytics.v_sales_by_month_country;
CREATE VIEW analytics.v_sales_by_month_country AS
SELECT
    EXTRACT(YEAR FROM order_date)::int          AS year,
    EXTRACT(MONTH FROM order_date)::int         AS month,
    date_trunc('month', order_date)::date       AS month_start,
    country,
    count(*)                                    AS orders,
    sum(amount)                                 AS total_amount,
    round(avg(amount), 2)                       AS avg_order_amount
FROM analytics.orders
WHERE status = 'completed'
GROUP BY 1, 2, 3, 4;

COMMENT ON VIEW analytics.v_sales_by_month_country IS
    'Completed sales per month and country: orders, total_amount, avg_order_amount';

-- Top 10 customers by completed sales.
-- Ties are broken by customer name so the list is deterministic and has exactly
-- 10 rows. main_country is the country the customer orders from most often.
DROP VIEW IF EXISTS analytics.v_top_customers;
CREATE VIEW analytics.v_top_customers AS
WITH totals AS (
    SELECT
        customer,
        mode() WITHIN GROUP (ORDER BY country) AS main_country,
        count(*)                               AS orders,
        sum(amount)                            AS total_amount
    FROM analytics.orders
    WHERE status = 'completed'
    GROUP BY customer
)
SELECT
    row_number() OVER (ORDER BY total_amount DESC, customer)::int AS customer_rank,
    customer,
    main_country,
    orders,
    total_amount
FROM totals
ORDER BY customer_rank
LIMIT 10;

COMMENT ON VIEW analytics.v_top_customers IS
    'Top 10 customers by completed sales: customer_rank, customer, main_country, orders, total_amount';
