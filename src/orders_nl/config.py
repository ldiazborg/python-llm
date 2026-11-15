"""Application settings, loaded from environment variables and `.env`.

Every tunable value of the project lives here, so the rest of the code never
reads `os.environ` directly. `pydantic-settings` maps each field to the
environment variable of the same name, case-insensitively (`pg_host` <->
`PG_HOST`), validates its type, and falls back to the default shown below.
See `.env.example` for the documented list of variables.

Two kinds of values live in this module:
  - module constants (`DB_SCHEMA`, `ALLOWED_TABLES`): part of the design, not
    configuration, so they are not overridable from the environment
  - `Settings` fields: deployment-specific (hosts, passwords, limits)
"""

from functools import lru_cache
from urllib.parse import quote_plus

from psycopg.conninfo import make_conninfo
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# Schema that holds the cleaned table and the views. It is created, together with
# the read-only role's grants, by docker/postgres/init/01-roles.sh.
DB_SCHEMA = "analytics"

# The only relations the LLM-generated SQL may read (enforced by the validator).
# Must match the table in sql/schema.sql and the views in sql/views.sql.
ALLOWED_TABLES: frozenset[str] = frozenset(
    {"orders", "v_sales_by_month_country", "v_top_customers"}
)


class Settings(BaseSettings):
    """Typed view of the environment (`.env` + real environment variables).

    Real environment variables win over values in `.env`. Secrets are
    `SecretStr`, so they are masked (`**********`) if the settings object is
    ever printed or logged; read them explicitly with `.get_secret_value()`.
    """

    # env_file: also read `.env` from the working directory.
    # extra="ignore": `.env` also holds docker-compose-only variables
    # (VLLM_IMAGE_TAG, HF_TOKEN, ...) that the app doesn't use; don't fail on them.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- PostgreSQL ------------------------------------------------------------
    pg_host: str = "localhost"
    postgres_port: int = 5432  # host port published by docker compose
    postgres_db: str = "orders"
    # Owner of the `analytics` schema: used by the ETL to create and load tables.
    postgres_user: str = "orders_admin"
    postgres_password: SecretStr = SecretStr("")
    # Read-only role used by the API to run LLM-generated SQL.
    reader_user: str = "orders_reader"
    reader_password: SecretStr = SecretStr("")

    # --- LLM (any OpenAI-compatible server; vLLM by default) -------------------
    llm_base_url: str = "http://localhost:8001/v1"
    llm_model: str = "qwen-coder"  # name the server exposes (vLLM --served-model-name)
    llm_api_key: SecretStr = SecretStr("")  # shared secret with vLLM's --api-key
    llm_timeout_s: float = 30.0  # per request; a slow local GPU needs some slack

    # --- Guardrails ------------------------------------------------------------
    # Maximum rows any generated query may return (the validator adds/clamps LIMIT).
    query_row_limit: int = 1000

    # Natural-language answers: max result rows shown to the answer-writing LLM
    # (keeps the prompt inside the model's context window).
    answer_max_rows: int = 20

    @property
    def admin_sqlalchemy_url(self) -> str:
        """Owner/ETL connection (SQLAlchemy URL, psycopg 3 driver).

        The password is URL-encoded because characters such as `@`, `/` or `:`
        would otherwise break the URL syntax.
        """
        pwd = quote_plus(self.postgres_password.get_secret_value())
        return (
            f"postgresql+psycopg://{self.postgres_user}:{pwd}"
            f"@{self.pg_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def reader_conninfo(self) -> str:
        """Read-only connection used by the API (libpq conninfo string).

        `make_conninfo` quotes and escapes each value correctly, so passwords
        with spaces or quotes are safe.
        """
        return make_conninfo(
            host=self.pg_host,
            port=self.postgres_port,
            dbname=self.postgres_db,
            user=self.reader_user,
            password=self.reader_password.get_secret_value(),
        )


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings, read once and then cached.

    Also used as a FastAPI dependency, which lets tests replace it through
    `app.dependency_overrides[get_settings]` without touching the environment.
    """
    return Settings()
