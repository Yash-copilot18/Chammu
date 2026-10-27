# app/database/connection.py

import pyodbc
import urllib
from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings
from pydantic import Field


class DatabaseSettings(BaseSettings):
    DB_SERVER: str = Field(..., env="DB_SERVER")
    DB_DATABASE: str = Field(..., env="DB_DATABASE")
    DB_DRIVER: str = Field(
        default="ODBC Driver 17 for SQL Server",
        env="DB_DRIVER"
    )

    # SQL Server Authentication, used only when DB_USERNAME is set.
    #
    # Windows Authentication remains the default and is the better choice where
    # it is available: nothing has to be stored. But an instance configured for
    # mixed mode and reached with a login such as `sa` cannot be opened with a
    # trusted connection at all, and a deployment on a machine whose Windows
    # account has no login on the server has no other route.
    #
    # The password is read from the environment and never written anywhere by
    # this application. `.env` is git-ignored; keep it that way.
    DB_USERNAME: Optional[str] = Field(default=None, env="DB_USERNAME")
    DB_PASSWORD: Optional[str] = Field(default=None, env="DB_PASSWORD")

    model_config = {"env_file": ".env", "extra": "allow"}


@lru_cache()
def get_db_settings() -> DatabaseSettings:
    return DatabaseSettings()


def build_connection_string() -> str:
    """
    Build a pyodbc connection string for the configured instance.

    Windows Authentication by default — nothing to store, nothing to leak.
    Setting DB_USERNAME switches to SQL Server Authentication, for an instance
    in mixed mode that is reached with a login rather than a domain account.

    The two are mutually exclusive in the string: sending both
    Trusted_Connection=yes and UID/PWD lets the driver choose, and which one it
    picks varies by version, so a connection that works on one machine fails on
    the next. The branch here makes the choice explicit.
    """
    settings = get_db_settings()

    base = (
        f"DRIVER={{{settings.DB_DRIVER}}};"
        f"SERVER={settings.DB_SERVER};"
        f"DATABASE={settings.DB_DATABASE};"
        f"TrustServerCertificate=yes;"
    )

    if settings.DB_USERNAME:
        if not settings.DB_PASSWORD:
            raise ValueError(
                "DB_USERNAME is set but DB_PASSWORD is empty. SQL Server "
                "Authentication needs both. Leave DB_USERNAME blank to use "
                "Windows Authentication instead."
            )
        return base + f"UID={settings.DB_USERNAME};PWD={settings.DB_PASSWORD};"

    return base + "Trusted_Connection=yes;"


def describe_auth() -> str:
    """How the connection authenticates, for the startup log. Never the password."""
    settings = get_db_settings()
    if settings.DB_USERNAME:
        return f"SQL Server Authentication as '{settings.DB_USERNAME}'"
    return "Windows Authentication"


def build_sqlalchemy_url() -> str:
    """
    Build the SQLAlchemy connection URL by URL-encoding the
    pyodbc connection string for use with create_engine().
    """
    connection_string = build_connection_string()
    encoded = urllib.parse.quote_plus(connection_string)
    return f"mssql+pyodbc:///?odbc_connect={encoded}"


def test_raw_connection() -> dict:
    """
    Test a raw pyodbc connection to SQL Server.
    Returns a dict with status and server info.
    Raises an exception if the connection fails.
    """
    conn_str = build_connection_string()
    try:
        conn = pyodbc.connect(conn_str, timeout=10)
        cursor = conn.cursor()
        cursor.execute("SELECT @@VERSION AS version, @@SERVERNAME AS server_name, DB_NAME() AS db_name")
        row = cursor.fetchone()
        result = {
            "status": "connected",
            "server_name": row.server_name,
            "database": row.db_name,
            "sql_server_version": row.version.split("\n")[0].strip(),
        }
        cursor.close()
        conn.close()
        return result
    except pyodbc.Error as e:
        raise ConnectionError(f"SQL Server connection failed: {str(e)}") from e