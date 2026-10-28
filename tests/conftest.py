"""Test fixtures for the database tests (marker `db`).

They run in their own database, named like the working one with `_test` added (`iris` ->
`iris_test`). It is created on first use, so the tests need no manual setup and never touch rows
that a real harvest wrote to the working database (several tests count rows).

Without a reachable database the tests are skipped; with REQUIRE_DB=1 they fail instead, so a
broken setup cannot look like a green run.
"""

import os
from pathlib import Path

import psycopg
import pytest
from dotenv import load_dotenv
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

ROOT = Path(__file__).resolve().parent.parent


def _unavailable(reason):
    if os.environ.get("REQUIRE_DB") == "1":
        pytest.fail(reason, pytrace=False)
    pytest.skip(reason)


@pytest.fixture(scope="session")
def test_db_url():
    load_dotenv(ROOT / ".env")
    url = os.environ.get("DATABASE_URL")
    if not url:
        _unavailable("DATABASE_URL is not set")
    name = conninfo_to_dict(url)["dbname"] + "_test"
    try:
        with psycopg.connect(make_conninfo(url, dbname="postgres"), autocommit=True) as admin:
            if not admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone():
                admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        test_url = make_conninfo(url, dbname=name)
        with psycopg.connect(test_url, autocommit=True) as conn:
            conn.execute((ROOT / "sql" / "01_schema.sql").read_text(encoding="utf-8"))
    except psycopg.Error as exc:
        _unavailable(f"database not reachable: {type(exc).__name__}")
    return test_url


@pytest.fixture
def conn(test_db_url):
    """A connection whose changes are rolled back at the end of the test."""
    connection = psycopg.connect(test_db_url)
    yield connection
    connection.rollback()
    connection.close()
