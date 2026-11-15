# Database (PostgreSQL 17)

## Objects (`src/orders_nl/sql/`)

- Schema `analytics` (created by the init script, owned by the admin user).
- `analytics.orders`: `order_id` PK, `amount numeric(12,2) >= 0`, `status` CHECK in the four `OrderStatus` values, indexes on `order_date` and `country`.
- `v_sales_by_month_country`: completed orders per month and country. Exposes `year` and `month` as **integers** on purpose so a small LLM can write `month = 3` instead of date arithmetic.
- `v_top_customers`: top 10 by completed sales; `row_number()` ordered by `total_amount DESC, customer` so there are always exactly 10 rows; `main_country` = `mode()` of the customer's countries.
- Views are `DROP VIEW IF EXISTS` + `CREATE VIEW` (not `CREATE OR REPLACE`) so column changes never fail. Business rule in both: only `status = 'completed'` is a sale.

## Two users, two access paths

| | Admin / owner (`POSTGRES_USER`) | Reader (`READER_USER`) |
|---|---|---|
| Used by | ETL only (`orders-load`, integration test setup) | API `/ask` only |
| Access | SQLAlchemy engine (`db.admin_engine`) | `db.make_readonly_runner` (psycopg, one connection per query) |
| Rights | creates/writes tables | `SELECT` on `analytics` via default privileges |
| Safety | — | role-level `default_transaction_read_only = on`, `statement_timeout = 5s`, `search_path = analytics`; the session is also set `read_only = True` |

The reader role is the second barrier behind the SQL validator: even a validator bug cannot write data.

## Init script (`docker/postgres/init/01-roles.sh`)

- Runs **only when the data volume is empty** (first start). Editing it has no effect on an existing volume: `docker compose down -v` (wipes data) then `up` and `orders-load` again.
- Uses psql variables (`:"ident"`, `:'literal'`) inside a quoted heredoc so values are escaped by psql, not the shell.
- Must keep LF line endings (`.gitattributes` enforces `*.sh eol=lf`); CRLF breaks it inside the Linux container.
- New tables/views created by the admin user become readable by the reader automatically (`ALTER DEFAULT PRIVILEGES`).

## Runner error translation (`db._translate_db_error`)

| SQLSTATE | App error | HTTP |
|---|---|---|
| class `42` (undefined column/table, syntax, permission), `22` (data exception), `21` (scalar subquery returned several rows) or `0A` (feature not supported) | `InvalidQueryError` with Postgres' primary message | 422 |
| `57014` (statement timeout) | `QueryTimeoutError` | 504 |
| anything else (connection refused, read-only violation...) | `QueryExecutionError` with a generic message; the cause is logged at ERROR by `orders_nl.db` (the only trace of it) | 500 |

Result shaping in the runner (`db.py`):

- Column names come from `cursor.description` (present even for 0 rows) and are made unique by `_unique_names` (`count`, `count` → `count`, `count_2`). Rows are fetched as tuples and zipped with those names — never use `dict_row` here: PostgreSQL allows repeated column names and a dict would silently drop one.
- `_to_json_friendly`: `numeric` with decimal places → `float` (amounts), without → `int` (`SUM` of a bigint and `EXTRACT(YEAR ...)` are `numeric` in PostgreSQL and must not come back as `862.0`); NaN/Infinity → text, because they have no JSON representation and would fail the whole response.
- The reader connection string carries `connect_timeout` (`config.DB_CONNECT_TIMEOUT_S`, 5 s) so an unreachable database fails fast instead of holding a worker thread for minutes.

## Local port

A native Windows PostgreSQL may already own 5432; then set `POSTGRES_PORT=5433` in `.env` (both compose and the app read it).

Compose publishes PostgreSQL (and vLLM) on the loopback addresses only, `127.0.0.1` **and** `[::1]`. Keep both entries in `ports:`:

- without an address Docker binds every interface and the database is reachable from the LAN with whatever password is in `.env`;
- with only `127.0.0.1`, every connection to `localhost` takes ~2 s longer on Windows, where `localhost` resolves to `::1` first and the refused IPv6 attempt has to time out (measured: the integration suite went from 8 s to minutes).
