"""Settings loaded from environment variables / `.env` (see `.env.example`)."""

from functools import lru_cache
from urllib.parse import quote_plus

from psycopg.conninfo import make_conninfo
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# Schema that holds the cleaned table and the views (created by docker/postgres/init).
DB_SCHEMA = "analytics"

# The only relations the LLM-generated SQL may read.
ALLOWED_TABLES: frozenset[str] = frozenset(
    {"orders", "v_sales_by_month_country", "v_top_customers"}
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # PostgreSQL
    pg_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "orders"
    postgres_user: str = "orders_admin"
    postgres_password: SecretStr = SecretStr("")
    reader_user: str = "orders_reader"
    reader_password: SecretStr = SecretStr("")

    # LLM (any OpenAI-compatible server; vLLM by default)
    llm_base_url: str = "http://localhost:8001/v1"
    llm_model: str = "qwen-coder"
    llm_api_key: SecretStr = SecretStr("")
    llm_timeout_s: float = 30.0

    # Guardrails
    query_row_limit: int = 1000

    @property
    def admin_sqlalchemy_url(self) -> str:
        """Owner/ETL connection (SQLAlchemy URL, psycopg 3 driver)."""
        pwd = quote_plus(self.postgres_password.get_secret_value())
        return (
            f"postgresql+psycopg://{self.postgres_user}:{pwd}"
            f"@{self.pg_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def reader_conninfo(self) -> str:
        """Read-only connection used by the API (libpq conninfo)."""
        return make_conninfo(
            host=self.pg_host,
            port=self.postgres_port,
            dbname=self.postgres_db,
            user=self.reader_user,
            password=self.reader_password.get_secret_value(),
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
