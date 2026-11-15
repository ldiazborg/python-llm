#!/bin/bash
# Runs once, on first start of an empty data volume.
# Creates the `analytics` schema (owned by the admin/ETL user) and a read-only
# role for the API. This is the database-side safety net behind the SQL validator.
#
# To re-run it (e.g. after changing the role): `docker compose down -v` deletes
# the data volume, then `docker compose up -d postgres` initializes it again.
# This wipes the loaded data; re-run `uv run orders-load` afterwards.

# -e: stop on the first error; -u: error on unset variables; -o pipefail: a
# failing command inside a pipe fails the whole pipe.
set -euo pipefail

# psql variables (-v name=value) are referenced in the SQL below as
#   :"name"  -> quoted identifier (role/database names)
#   :'name'  -> quoted literal (the password)
# so values are escaped by psql, never pasted raw into the SQL.
# The heredoc delimiter is quoted ('EOSQL'), so the shell does NOT expand
# anything inside it; only psql interpolates the :variables.
psql -v ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v db_name="$POSTGRES_DB" \
  -v reader_user="$READER_USER" \
  -v reader_password="$READER_PASSWORD" <<'EOSQL'
-- Schema for the cleaned table and the views, owned by the admin (ETL) user.
CREATE SCHEMA IF NOT EXISTS analytics AUTHORIZATION CURRENT_USER;

-- Login role used by the API to run LLM-generated SQL.
CREATE ROLE :"reader_user" LOGIN PASSWORD :'reader_password';
-- Every transaction of this role is read-only: INSERT/UPDATE/DELETE/DDL fail.
ALTER ROLE :"reader_user" SET default_transaction_read_only = on;
-- Runaway queries (huge joins, pg_sleep...) are cancelled after 5 seconds.
ALTER ROLE :"reader_user" SET statement_timeout = '5s';
-- Unqualified names ("orders") resolve to analytics.orders.
ALTER ROLE :"reader_user" SET search_path = analytics;

-- Minimum privileges: connect, see the schema, read its tables. Nothing else.
GRANT CONNECT ON DATABASE :"db_name" TO :"reader_user";
GRANT USAGE ON SCHEMA analytics TO :"reader_user";
-- Tables/views created later by the ETL (as the admin user) become readable automatically.
ALTER DEFAULT PRIVILEGES IN SCHEMA analytics GRANT SELECT ON TABLES TO :"reader_user";
EOSQL
