"""Settings: value ranges and the two connection strings built from them."""

import pytest
from pydantic import ValidationError
from sqlalchemy import make_url

from orders_nl.config import Settings


def _settings(**overrides) -> Settings:
    """Settings from defaults + overrides only (never the developer's .env)."""
    return Settings(_env_file=None, **overrides)


@pytest.mark.parametrize(
    "overrides",
    [
        {"query_row_limit": 0},  # would render LIMIT 0: every answer empty
        {"query_row_limit": -5},  # would render LIMIT -5: every query a database error
        {"answer_max_rows": 0},
        {"llm_timeout_s": 0},
        {"postgres_port": 70000},
    ],
)
def test_out_of_range_settings_fail_at_startup(overrides: dict) -> None:
    """A bad value is reported when settings load, naming the field, not on the first request."""
    with pytest.raises(ValidationError, match=next(iter(overrides))):
        _settings(**overrides)


def test_admin_url_escapes_special_characters() -> None:
    """A password with URL syntax characters survives the round trip through the URL."""
    password = "p@ss/w:rd%1 #x"
    settings = _settings(postgres_password=password, postgres_user="orders admin")
    url = make_url(settings.admin_sqlalchemy_url)
    assert (url.username, url.password) == ("orders admin", password)
    assert (url.drivername, url.host, url.port, url.database) == ("postgresql+psycopg", "localhost", 5432, "orders")


def test_reader_conninfo_escapes_special_characters() -> None:
    """The API's libpq string quotes values, so spaces and quotes in a password are safe."""
    from psycopg.conninfo import conninfo_to_dict

    settings = _settings(reader_password="it's a secret")
    assert conninfo_to_dict(settings.reader_conninfo)["password"] == "it's a secret"
