#!/bin/bash
# Runs once, on first start of an empty data volume.
# Creates the `analytics` schema (owned by the admin/ETL user) and a read-only
# role for the API. This is the database-side safety net behind the SQL validator.
set -euo pipefail

psql -v ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v db_name="$POSTGRES_DB" \
  -v reader_user="$READER_USER" \
  -v reader_password="$READER_PASSWORD" <<'EOSQL'
CREATE SCHEMA IF NOT EXISTS analytics AUTHORIZATION CURRENT_USER;

CREATE ROLE :"reader_user" LOGIN PASSWORD :'reader_password';
ALTER ROLE :"reader_user" SET default_transaction_read_only = on;
ALTER ROLE :"reader_user" SET statement_timeout = '5s';
ALTER ROLE :"reader_user" SET search_path = analytics;

GRANT CONNECT ON DATABASE :"db_name" TO :"reader_user";
GRANT USAGE ON SCHEMA analytics TO :"reader_user";
-- Tables/views created later by the ETL (as the admin user) become readable automatically.
ALTER DEFAULT PRIVILEGES IN SCHEMA analytics GRANT SELECT ON TABLES TO :"reader_user";
EOSQL
