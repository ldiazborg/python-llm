# SQL validation (`validator.py`)

LLM-generated SQL is untrusted input. It is parsed with **sqlglot** (dialect `postgres`) and every rule walks the whole AST — never use regexes for this (CTEs, subqueries and comments defeat text matching).

## `SqlValidator(policy, rules).validate(sql) -> str`

1. Parse: non-empty, ≤ `MAX_SQL_LENGTH` (5000), exactly one statement (trailing `;` is fine; `None` entries from `sqlglot.parse` are dropped), unwrap a bare `(SELECT ...)`. Parentheses that carry modifiers — `(SELECT ...) ORDER BY x LIMIT 5` — are kept as an `exp.Subquery` root: unwrapping would drop the ORDER BY/LIMIT. Parse errors become a clean message with line/column only (sqlglot's raw message contains ANSI colors and token reprs).
2. Run `DEFAULT_RULES` in order; the first failing rule's message is returned to the client.
3. `_enforce_limit`: keep a literal `LIMIT n`/`FETCH FIRST n ROWS ONLY` if `n <= row_limit`, otherwise set `LIMIT row_limit` (also for `LIMIT ALL`, non-literal limits and `FETCH ... WITH TIES`/`PERCENT`, which don't bound the result to `n` rows).
4. Return `tree.sql(dialect="postgres")` — this normalized string is what gets executed and returned as `sql` in the API response.

## Rules (`DEFAULT_RULES`)

| Rule | Purpose |
|---|---|
| `require_query_root` | root must be `exp.Select` or `exp.SetOperation`, possibly inside a parenthesised `exp.Subquery` (free text like "this is not sql" parses as `Not` and is rejected here) |
| `forbid_side_effects` | no `Insert/Update/Delete/Merge/Create/Drop/Alter/Copy/Command/Into/Lock/Set/...` **anywhere** (catches `WITH x AS (DELETE ...)`, `SELECT ... INTO`, `FOR UPDATE`) |
| `restrict_tables` | every `exp.Table` must be in `allowed_tables` (or a CTE **in scope**); qualified names only with the app schema; no catalog; empty table name = table function → rejected |
| `require_data_source` | at least one real allowed table must be read (added after the real model answered "¿Qué tiempo hace?" with `SELECT CURRENT_TIMESTAMP`) |
| `forbid_dangerous_functions` | blocklist: prefixes `pg_`, `lo_`, `dblink`, `file_`, `has_`, `txid_`; any name containing `_to_xml`; names like `set_config`, `current_setting`, `version`, `ts_stat`, `current_database`; bare keywords `user`, `current_role` |

Gotchas:

- Node classes are resolved by name (`_node_types`) because sqlglot renames them across versions (`AlterTable` → `Alter`); missing names are skipped silently. Same for the `with`/`with_` argument (`_with_clause`) and the `WITH TIES` flag (`_literal_row_count`).
- **Compare names the way PostgreSQL reads them** (`_identifier_name`): unquoted identifiers fold to lower case, quoted ones keep their case. `FROM ORDERS` is the allowed table; `"ORDERS"` is not.
- **A CTE name is not query-wide** (`_cte_names_in_scope`). It only hides a real relation where PostgreSQL can see the CTE: in the query owning the `WITH`, in later sibling CTEs, and in its own body only under `WITH RECURSIVE`. `WITH pg_roles AS (SELECT * FROM pg_roles) ...` reads the catalog inside the body, so it fails `restrict_tables`. `WITH orders AS (SELECT 1) SELECT * FROM orders` reads no real table and fails `require_data_source`.
- Known functions are typed nodes whose canonical name can differ from PostgreSQL's (`version()` → `exp.CurrentVersion`, "CURRENT_VERSION"); unknown ones are `exp.Anonymous`. `_function_names` checks both the parsed name and the name in the rendered SQL.
- Functions taking SQL or a relation name **as text** (`query_to_xml*`, `table_to_xml*`, `ts_stat`) sidestep `restrict_tables`; block any new one you find. The blocklist is best-effort by design — the read-only role's grants are the real boundary.

## Adding a rule

Write `def rule(tree: exp.Expression, policy: QueryPolicy) -> None` raising `InvalidQueryError("human-readable reason")`, then either append it to `DEFAULT_RULES` or pass `rules=(*DEFAULT_RULES, rule)`. Add cases to the `VALID` / `INVALID` lists in `tests/test_validator.py` (each invalid case should fail for the intended reason — print the messages to check).
