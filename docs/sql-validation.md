# SQL validation (`validator.py`)

LLM-generated SQL is untrusted input. It is parsed with **sqlglot** (dialect `postgres`) and every rule walks the whole AST — never use regexes for this (CTEs, subqueries and comments defeat text matching).

## `SqlValidator(policy, rules).validate(sql) -> str`

1. Parse: non-empty, ≤ `MAX_SQL_LENGTH` (5000), exactly one statement (trailing `;` is fine; `None` entries from `sqlglot.parse` are dropped), unwrap `(SELECT ...)`. Parse errors become a clean message with line/column only (sqlglot's raw message contains ANSI colors and token reprs).
2. Run `DEFAULT_RULES` in order; the first failing rule's message is returned to the client.
3. `_enforce_limit`: keep a literal `LIMIT n`/`FETCH FIRST n` if `n <= row_limit`, otherwise set `LIMIT row_limit` (also for `LIMIT ALL` or non-literal limits).
4. Return `tree.sql(dialect="postgres")` — this normalized string is what gets executed and returned as `sql` in the API response.

## Rules (`DEFAULT_RULES`)

| Rule | Purpose |
|---|---|
| `require_query_root` | root must be `exp.Select` or `exp.SetOperation` (free text like "this is not sql" parses as `Not` and is rejected here) |
| `forbid_side_effects` | no `Insert/Update/Delete/Merge/Create/Drop/Alter/Copy/Command/Into/Lock/Set/...` **anywhere** (catches `WITH x AS (DELETE ...)`, `SELECT ... INTO`, `FOR UPDATE`) |
| `restrict_tables` | every `exp.Table` must be in `allowed_tables` (or a CTE of the query); qualified names only with the app schema; no catalog; empty table name = table function → rejected |
| `require_data_source` | at least one real allowed table must be read (added after the real model answered "¿Qué tiempo hace?" with `SELECT CURRENT_TIMESTAMP`) |
| `forbid_dangerous_functions` | blocklist: prefixes `pg_`, `lo_`, `dblink`, `file_` plus names like `set_config`, `current_setting`, `version` |

Gotchas:

- Node classes are resolved by name (`_node_types`) because sqlglot renames them across versions (`AlterTable` → `Alter`); missing names are skipped silently.
- An unqualified name that matches a CTE resolves to the CTE (`_is_cte_reference`): `WITH orders AS (SELECT 1) SELECT * FROM orders` reads no real table and fails `require_data_source`; a CTE body reading `pg_shadow` still fails `restrict_tables`.
- Known functions are typed nodes (`func.sql_name()`), unknown ones are `exp.Anonymous` (`func.name`).

## Adding a rule

Write `def rule(tree: exp.Expression, policy: QueryPolicy) -> None` raising `InvalidQueryError("human-readable reason")`, then either append it to `DEFAULT_RULES` or pass `rules=(*DEFAULT_RULES, rule)`. Add cases to the `VALID` / `INVALID` lists in `tests/test_validator.py` (each invalid case should fail for the intended reason — print the messages to check).
