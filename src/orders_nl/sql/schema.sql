-- Clean orders table. Executed by `orders-load` as the admin (owner) user.
-- The `analytics` schema and the read-only role's default SELECT grant are
-- created by docker/postgres/init/01-roles.sh, so this table is readable by the
-- API role automatically.
--
-- Idempotent: safe to run on every load.

CREATE TABLE IF NOT EXISTS analytics.orders (
    order_id   text           PRIMARY KEY,
    order_date date           NOT NULL,
    customer   text           NOT NULL,
    country    text           NOT NULL,
    product    text           NOT NULL,
    amount     numeric(12, 2) NOT NULL CHECK (amount >= 0),
    -- Keep in sync with orders_nl.domain.OrderStatus.
    status     text           NOT NULL
        CHECK (status IN ('completed', 'pending', 'cancelled', 'refunded'))
);

COMMENT ON TABLE analytics.orders IS 'One row per order, cleaned by orders_nl.etl.clean';
COMMENT ON COLUMN analytics.orders.amount IS 'Order total in EUR';
COMMENT ON COLUMN analytics.orders.status IS 'completed | pending | cancelled | refunded; only completed counts as a sale';

-- The two most common filters in questions ("en marzo", "en España").
CREATE INDEX IF NOT EXISTS orders_order_date_idx ON analytics.orders (order_date);
CREATE INDEX IF NOT EXISTS orders_country_idx ON analytics.orders (country);
