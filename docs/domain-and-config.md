# Domain vocabulary and configuration

## `domain.py` — single source of truth

| Name | Used by |
|---|---|
| `COLUMNS` | generator output, cleaner contract, loader column order |
| `OrderStatus` (StrEnum) | generator weights, cleaner validation, SQL prompt |
| `SALE_STATUSES` (`completed` only) | SQL prompt ("ventas"); views.sql hardcodes the same rule |
| `KNOWN_COUNTRIES` | cleaner canonicalization, SQL prompt, generator catalog test |

## Things that must change together

Python reads the domain automatically, but SQL files and some lists cannot:

| Change | Update all of |
|---|---|
| Add/rename a status | `domain.OrderStatus`, `sql/schema.sql` CHECK constraint, `sql/views.sql` (`status = 'completed'`), `generation/catalog.DEFAULT_STATUS_WEIGHTS` |
| Add a country | `domain.KNOWN_COUNTRIES`, `generation/catalog.DEFAULT_COUNTRIES` (with a Faker locale that exists — e.g. there is no `es_PE`) |
| Add a column | `domain.COLUMNS`, `sql/schema.sql`, generator records, `llm/prompt.SCHEMA_DESCRIPTION` |
| Add a queryable table/view | `sql/*.sql`, `config.ALLOWED_TABLES`, `llm/prompt.SCHEMA_DESCRIPTION` (+ few-shot example if useful) |
| Add a setting | `config.Settings` field, `.env.example` (documented), README configuration table |

Schema changes to an existing table need `DROP`/migration: `schema.sql` only does `CREATE TABLE IF NOT EXISTS`.

## `config.py`

- Module constants (not env-overridable, they are design): `DB_SCHEMA = "analytics"`, `ALLOWED_TABLES`, `DB_CONNECT_TIMEOUT_S`.
- `Settings` (pydantic-settings): reads `.env` + environment (env wins), case-insensitive names, `extra="ignore"` because `.env` also holds compose-only variables (`VLLM_*`, `HF_TOKEN`, `LLM_MODEL_ID`).
- Secrets are `SecretStr`; call `.get_secret_value()` explicitly.
- Numeric settings declare their range with `Field(ge=..., gt=...)` (`query_row_limit`, `answer_max_rows` ≥ 1, `llm_timeout_s` > 0, `postgres_port` 1..65535) so a bad value fails at startup naming the variable. Give any new numeric setting a range too.
- Two connection strings: `admin_sqlalchemy_url` (ETL, `postgresql+psycopg://`, built with `sqlalchemy.URL.create` so every part is percent-encoded) and `reader_conninfo` (API, libpq conninfo via `make_conninfo`, with `connect_timeout=DB_CONNECT_TIMEOUT_S`). Never build either by string formatting.
- `get_settings()` is `lru_cache`d and is itself a FastAPI dependency; tests override it with `Settings(_env_file=None)` so the developer's `.env` never leaks into tests.

`.env.example` documents every variable; the README has a table with defaults and which side (compose/app) reads each one.
