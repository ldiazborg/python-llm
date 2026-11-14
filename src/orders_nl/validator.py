"""Validate LLM-generated SQL before execution (requirement 3). Uses sqlglot's AST."""

from collections.abc import Collection


class InvalidQueryError(ValueError):
    """The SQL is not a single, read-only query over the allowed tables."""


def validate_sql(sql: str, allowed_tables: Collection[str], *, row_limit: int) -> str:
    """Return SQL that is safe to run, or raise InvalidQueryError with a clear reason.

    Parse with `sqlglot.parse(sql, read="postgres")` and enforce:
      - exactly one statement; unparseable input -> InvalidQueryError
      - root is a read query (exp.Select / set operations like UNION); reject
        INSERT/UPDATE/DELETE/MERGE/DDL/COPY/SET/CALL/etc.
      - no data-modifying statements anywhere in the tree (e.g. inside CTEs)
      - every exp.Table in the tree is in `allowed_tables` (CTE names declared in the
        same query are allowed); a schema qualifier, if present, must be the app schema
      - no dangerous functions (pg_sleep, pg_read_file, dblink, lo_*, set_config, ...)
      - add/clamp LIMIT to `row_limit`
    Return the normalized SQL (`expression.sql(dialect="postgres")`).
    """
    raise NotImplementedError("TODO: implement validate_sql")
