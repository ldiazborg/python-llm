-- Cleaned orders table. Executed by `orders-load` as the admin user.
-- The `analytics` schema is created by docker/postgres/init/01-roles.sh.
CREATE TABLE IF NOT EXISTS analytics.orders (
    order_id   text           PRIMARY KEY,
    order_date date           NOT NULL,
    customer   text           NOT NULL,
    country    text           NOT NULL,
    product    text           NOT NULL,
    amount     numeric(12, 2) NOT NULL CHECK (amount >= 0),
    status     text           NOT NULL
);

-- TODO: decide on a CHECK constraint / enum for `status` once the generator's
--       status values are fixed (e.g. 'completed', 'pending', 'cancelled', 'refunded').
-- TODO: indexes for the common filters (order_date, country) if data grows.
