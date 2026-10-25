"""Checks the database login rules from inside the app container. Prints no secrets.

    docker compose exec -T app python scripts/db/check_auth.py

Every line says what was tried, what was expected and what happened. Exits
with 1 if anything behaved differently from what pg_hba.conf and
scripts/db/setup-roles.sql intend.
"""

import os
import sys

import psycopg
from psycopg.conninfo import make_conninfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from app.vectorstore import database_dsn  # noqa: E402

WRONG_PASSWORD = "definitely-not-the-password"
failures = 0


def attempt(label: str, dsn: str, expect_ok: bool, query: str = "SELECT current_user, current_database(), count(*) FROM chunks"):
    global failures
    try:
        with psycopg.connect(dsn, connect_timeout=10) as conn:
            outcome = f"connected: {conn.execute(query).fetchone()}"
            ok = True
    except psycopg.Error as error:
        outcome = "rejected: " + str(error).strip().splitlines()[0].split(" failed: ", 1)[-1][:110]
        ok = False
    passed = ok == expect_ok
    failures += not passed
    print(f"[{'PASS' if passed else 'FAIL'}] {label}: {outcome}")


def denied_ddl(label: str, dsn: str, statement: str):
    global failures
    with psycopg.connect(dsn, connect_timeout=10) as conn:
        try:
            conn.execute(statement)
            outcome, passed = "ALLOWED", False
        except psycopg.errors.InsufficientPrivilege as error:
            outcome, passed = "denied: " + str(error).strip().splitlines()[0], True
        conn.rollback()
    failures += not passed
    print(f"[{'PASS' if passed else 'FAIL'}] {label}: {outcome}")


app, test = database_dsn("APP"), database_dsn("TEST")
for name in ("APP_DB_PASSWORD", "TEST_DB_PASSWORD"):
    print(f"{name} set in this container: {bool(os.environ.get(name))}")

attempt("app role, no password", make_conninfo(app, password=""), False)
attempt("app role, wrong password", make_conninfo(app, password=WRONG_PASSWORD), False)
attempt("app role, its own password", app, True)
attempt("app role -> test database", make_conninfo(app, dbname="crm_assistant_test"), False)
attempt("test role, no password", make_conninfo(test, password=""), False)
attempt("test role, wrong password", make_conninfo(test, password=WRONG_PASSWORD), False)
attempt("test role, its own password", test, True)
attempt("test role -> app database", make_conninfo(test, dbname="crm_assistant"), False)
attempt("admin role over the network, no password", make_conninfo(app, user="crm_assistant", password=""), False)
attempt("admin role over the network, wrong password", make_conninfo(app, user="crm_assistant", password=WRONG_PASSWORD), False)
denied_ddl("app role creates a table", app, "CREATE TABLE not_allowed (id integer)")
denied_ddl("app role drops chunks", app, "DROP TABLE chunks")
denied_ddl("test role drops chunks", test, "DROP TABLE chunks")

print("all checks passed" if not failures else f"{failures} check(s) FAILED")
sys.exit(1 if failures else 0)
