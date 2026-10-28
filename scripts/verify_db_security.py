"""
Phase 2 - Verify the agent's database permissions.

Logs in as the AI agent's read-only user (USR_FDE_RO) and checks that it can
read the clean view but cannot touch the raw legacy table or change anything.

Every check runs inside a transaction that is always rolled back, so even if
permissions were wrong, nothing would actually be changed or dropped.

Usage:
    python scripts/verify_db_security.py
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import pyodbc
from dotenv import load_dotenv

from exception import ConfigError

# __file__ is 'scripts/verify_db_security.py'
PROJECT_ROOT = Path(__file__).resolve().parents[1]

logger = logging.getLogger("db_security_check")


@dataclass(frozen=True)
class SecurityCheck:
    """One SQL statement and whether the agent user should be allowed to run it."""

    name: str
    sql: str
    should_succeed: bool


CHECKS = (
    SecurityCheck("Read the clean view",
                  "SELECT TOP 5 * FROM FDE_VIEWS.VW_ACTIVE_FLEET", True),
    SecurityCheck("Read the raw legacy table",
                  "SELECT TOP 5 * FROM dbo.TBL_SC_FLEET_HIST_RAW", False),
    SecurityCheck("Update data through the view",
                  "UPDATE FDE_VIEWS.VW_ACTIVE_FLEET SET cargo_temp_c = 0", False),
    SecurityCheck("Delete rows from the raw table",
                  "DELETE FROM dbo.TBL_SC_FLEET_HIST_RAW", False),
    SecurityCheck("Drop the raw table",
                  "DROP TABLE dbo.TBL_SC_FLEET_HIST_RAW", False),
    SecurityCheck("Create a new table",
                  "CREATE TABLE dbo.AGENT_TEST (id INT)", False),
)


def build_connection_string() -> str:
    """Connection string for the agent's read-only login, from .env."""
    load_dotenv(PROJECT_ROOT / ".env")

    user = os.getenv("AGENT_DB_USER")
    password = os.getenv("AGENT_DB_PASSWORD")
    if not user or not password:
        raise ConfigError("AGENT_DB_USER / AGENT_DB_PASSWORD are missing. Add them to the .env file.")

    return (
        "DRIVER={ODBC Driver 18 for SQL Server};"
        f"SERVER={os.getenv('SQL_SERVER_HOST', 'localhost')},{os.getenv('SQL_SERVER_PORT', '1433')};"
        "DATABASE=master;"
        f"UID={user};"
        f"PWD={password};"
        "Encrypt=no;"
        "TrustServerCertificate=yes;"
    )


def run_check(conn: pyodbc.Connection, check: SecurityCheck) -> bool:
    """Run one check and return True if the outcome matches what we expect."""
    cursor = conn.cursor()
    try:
        cursor.execute(check.sql)
        if cursor.description:  # a SELECT: show how many rows came back
            detail = f"{len(cursor.fetchall())} rows returned"
        else:
            detail = "statement executed"
        allowed = True
    except pyodbc.Error as exc:
        detail = str(exc.args[1]).split("]")[-1].split("(")[0].strip()  # just the SQL Server message
        allowed = False
    finally:
        conn.rollback()  # never keep any change, even if a permission was wrong

    passed = allowed == check.should_succeed
    expected = "ALLOWED" if check.should_succeed else "DENIED"
    actual = "ALLOWED" if allowed else "DENIED"
    log = logger.info if passed else logger.error
    log("%s %-32s expected %-7s got %-7s | %s",
        "✅" if passed else "❌", check.name, expected, actual, detail)
    return passed


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

    try:
        conn = pyodbc.connect(build_connection_string(), autocommit=False)
    except ConfigError as exc:
        logger.error(exc)
        return 1
    except pyodbc.Error as exc:
        if exc.args and exc.args[0] == "IM002":
            logger.error("ODBC Driver 18 for SQL Server is not installed on this machine.")
        else:
            # Usually: database not running, or setup_security_and_view.sql not run yet (login missing)
            logger.error("Could not log in as the agent user: %s", exc)
        return 1

    with conn:
        results = [run_check(conn, check) for check in CHECKS]

    passed = sum(results)
    if passed == len(CHECKS):
        logger.info("✅ All %d security checks passed: the agent user is read-only.", len(CHECKS))
        return 0
    logger.error("❌ %d of %d security checks failed. Review the permissions.", len(CHECKS) - passed, len(CHECKS))
    return 1


if __name__ == "__main__":
    sys.exit(main())
