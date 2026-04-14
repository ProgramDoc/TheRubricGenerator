"""
Database compatibility layer — supports both PostgreSQL and SQLite.

When DATABASE_URL is set, uses PostgreSQL via psycopg2.
Otherwise, falls back to SQLite (for local dev and tests).
"""

import os
import re
import sqlite3
import logging

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL")

# ---------------------------------------------------------------------------
# Detect which driver to use
# ---------------------------------------------------------------------------
_USE_PG = bool(DATABASE_URL)

if _USE_PG:
    import psycopg2
    import psycopg2.extras
    import psycopg2.errors
    # Re-export for exception handling in app code
    IntegrityError = psycopg2.IntegrityError
else:
    IntegrityError = sqlite3.IntegrityError


def _pg_ddl_to_sqlite(sql):
    """Convert PostgreSQL DDL to SQLite-compatible DDL at runtime.
    Applied only when running in SQLite mode (no DATABASE_URL)."""
    import re
    # SERIAL PRIMARY KEY → INTEGER PRIMARY KEY AUTOINCREMENT
    sql = re.sub(r'\bSERIAL\s+PRIMARY\s+KEY\b', 'INTEGER PRIMARY KEY AUTOINCREMENT', sql, flags=re.I)
    # TIMESTAMP DEFAULT CURRENT_TIMESTAMP → TEXT DEFAULT (datetime('now'))
    sql = re.sub(r'\bTIMESTAMP\s+DEFAULT\s+CURRENT_TIMESTAMP\b', "TEXT DEFAULT (datetime('now'))", sql, flags=re.I)
    # TIMESTAMP without default → TEXT
    sql = re.sub(r'\bTIMESTAMP\b', 'TEXT', sql, flags=re.I)
    # ILIKE → LIKE (SQLite LIKE is case-insensitive for ASCII)
    sql = re.sub(r'\bILIKE\b', 'LIKE', sql, flags=re.I)
    return sql


def _convert_params(sql):
    """Convert SQLite-style ? placeholders to PostgreSQL-style %s."""
    # Only replace ? that are parameter markers (not inside quotes)
    result = []
    in_single = False
    in_double = False
    for ch in sql:
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == '?' and not in_single and not in_double:
            result.append('%s')
            continue
        result.append(ch)
    return ''.join(result)


# ---------------------------------------------------------------------------
# PostgreSQL wrapper classes
# ---------------------------------------------------------------------------

class PgCursor:
    """Wraps a psycopg2 cursor to provide sqlite3-compatible attributes."""

    def __init__(self, cursor):
        self._cur = cursor
        self.lastrowid = None
        self.rowcount = cursor.rowcount

    @property
    def description(self):
        return self._cur.description

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    def __iter__(self):
        return iter(self._cur)


class PgConnection:
    """Wraps a psycopg2 connection to mimic the sqlite3 API."""

    def __init__(self, dsn):
        self._conn = psycopg2.connect(dsn)
        self._conn.autocommit = False

    def execute(self, sql, params=None):
        cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        pg_sql = _convert_params(sql)
        cur.execute(pg_sql, params or ())
        result = PgCursor(cur)
        # Populate lastrowid if this was an INSERT with RETURNING
        if 'RETURNING' in pg_sql.upper() and cur.description:
            try:
                row = cur.fetchone()
                if row:
                    # Get first column value (the returned id)
                    result.lastrowid = list(row.values())[0] if isinstance(row, dict) else row[0]
            except Exception:
                pass
        return result

    def executescript(self, sql):
        """Execute multiple SQL statements as a single block."""
        cur = self._conn.cursor()
        cur.execute(sql)
        return cur

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self._conn.rollback()
        return False


# ---------------------------------------------------------------------------
# SQLite wrapper — converts PG-style DDL back to SQLite at runtime
# ---------------------------------------------------------------------------

class SqliteConnection:
    """Thin wrapper around sqlite3.Connection that converts PG DDL to SQLite."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=None):
        sql = _pg_ddl_to_sqlite(sql)
        # Convert CURRENT_TIMESTAMP in VALUES/SET to datetime('now') for SQLite
        sql = re.sub(r'\bCURRENT_TIMESTAMP\b', "datetime('now')", sql)
        # Strip RETURNING clause — SQLite cursor.lastrowid works natively
        sql = re.sub(r'\s+RETURNING\s+\w+\s*$', '', sql, flags=re.I)
        if params:
            return self._conn.execute(sql, params)
        return self._conn.execute(sql)

    def executescript(self, sql):
        sql = _pg_ddl_to_sqlite(sql)
        sql = re.sub(r'\bCURRENT_TIMESTAMP\b', "datetime('now')", sql)
        return self._conn.executescript(sql)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return self._conn.__exit__(*a)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_db(db_path=None):
    """
    Return a database connection.

    - If DATABASE_URL is set: returns a PgConnection (PostgreSQL).
    - Otherwise: returns a SqliteConnection (wraps sqlite3) with WAL mode.
    """
    if _USE_PG:
        return PgConnection(DATABASE_URL)
    else:
        path = str(db_path) if db_path else str(
            os.environ.get("SQLITE_DB_PATH", "rubricgen.db")
        )
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return SqliteConnection(conn)


def is_postgres():
    """Check if we're using PostgreSQL."""
    return _USE_PG


def column_exists(conn, table, column):
    """Check if a column exists in a table (works with both PG and SQLite)."""
    if _USE_PG:
        row = conn.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_name = ? AND column_name = ?",
            (table, column),
        ).fetchone()
        return row is not None
    else:
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        return column in cols
