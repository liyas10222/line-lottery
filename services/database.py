import logging
import re
import sqlite3
from pathlib import Path

from config import Config


try:
    import psycopg2
    import psycopg2.extras
except ImportError:  # PostgreSQL is optional unless DATABASE_URL is configured.
    psycopg2 = None


LOGGER = logging.getLogger(__name__)
IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class DatabaseUnavailable(RuntimeError):
    pass


class CursorWrapper:
    def __init__(self, cursor, lastrowid=None):
        self._cursor = cursor
        self.lastrowid = lastrowid if lastrowid is not None else getattr(cursor, "lastrowid", None)

    @property
    def rowcount(self):
        return self._cursor.rowcount

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()


class DatabaseConnection:
    def __init__(self, conn, dialect):
        self.conn = conn
        self.dialect = dialect

    def __enter__(self):
        return self

    def __exit__(self, exc_type, _exc, _traceback):
        try:
            if exc_type is not None:
                self.rollback()
        finally:
            self.close()
        return False

    @property
    def is_postgres(self):
        return self.dialect == "postgres"

    @property
    def is_sqlite(self):
        return self.dialect == "sqlite"

    def execute(self, sql, params=None):
        cursor = self.conn.cursor()
        sql = self._prepare_sql(sql)
        prepared_params = self._prepare_params(params)
        if prepared_params is None:
            cursor.execute(sql)
        else:
            cursor.execute(sql, prepared_params)
        return CursorWrapper(cursor)

    def execute_insert(self, sql, params=None, returning="id"):
        if self.is_postgres and returning:
            sql_to_run = sql.strip().rstrip(";")
            if " RETURNING " not in sql_to_run.upper():
                sql_to_run = f"{sql_to_run} RETURNING {returning}"
            cursor = self.conn.cursor()
            prepared_params = self._prepare_params(params)
            if prepared_params is None:
                cursor.execute(self._prepare_sql(sql_to_run))
            else:
                cursor.execute(self._prepare_sql(sql_to_run), prepared_params)
            row = cursor.fetchone()
            try:
                lastrowid = row[returning] if row else None
            except (KeyError, TypeError):
                lastrowid = row[0] if row else None
            return CursorWrapper(cursor, lastrowid=lastrowid)

        return self.execute(sql, params)

    def begin_immediate(self):
        if self.is_postgres:
            self.execute("BEGIN")
        else:
            self.execute("BEGIN IMMEDIATE")

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        self.conn.close()

    def _prepare_sql(self, sql):
        if self.is_sqlite:
            return sql

        prepared = sql
        prepared = re.sub(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", "INSERT INTO", prepared, flags=re.IGNORECASE)
        if "INSERT OR IGNORE" in sql.upper() and "ON CONFLICT" not in prepared.upper():
            prepared = prepared.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
        return prepared.replace("?", "%s")

    @staticmethod
    def _prepare_params(params):
        if params is None:
            return None
        if isinstance(params, tuple):
            return params
        if isinstance(params, list):
            return tuple(params)
        return params


def database_mode():
    return "postgres" if Config.DATABASE_URL else "sqlite"


def get_db():
    if Config.DATABASE_URL:
        if psycopg2 is None:
            raise DatabaseUnavailable("DATABASE_URL is configured but psycopg2-binary is not installed")
        conn = psycopg2.connect(Config.DATABASE_URL, cursor_factory=psycopg2.extras.DictCursor)
        return DatabaseConnection(conn, "postgres")

    db_path = Config.database_file()
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return DatabaseConnection(conn, "sqlite")


def validate_identifier(value):
    if not IDENTIFIER_RE.match(value):
        raise ValueError(f"Unsafe SQL identifier: {value}")
    return value


def table_columns(db, table_name):
    table_name = validate_identifier(table_name)
    if db.is_postgres:
        rows = db.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = ?
            """,
            (table_name,),
        ).fetchall()
        return {row["column_name"] for row in rows}

    return {row["name"] for row in db.execute(f"PRAGMA table_info({table_name})").fetchall()}


def ensure_column(db, table_name, column_name, definition):
    table_name = validate_identifier(table_name)
    column_name = validate_identifier(column_name)
    if column_name not in table_columns(db, table_name):
        db.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")


def create_schema(db):
    if db.is_postgres:
        create_postgres_schema(db)
    else:
        create_sqlite_schema(db)
    create_indexes(db)


def create_sqlite_schema(db):
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            line_user_id TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL,
            picture_url TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS prizes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            code TEXT NOT NULL UNIQUE,
            short_label TEXT,
            weight REAL NOT NULL,
            stock INTEGER,
            requires_serial INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS lottery_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            line_user_id TEXT NOT NULL,
            line_display_name TEXT,
            prize_id INTEGER,
            prize_serial_id INTEGER,
            prize_name TEXT NOT NULL,
            prize_code TEXT NOT NULL,
            serial_code TEXT,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (prize_id) REFERENCES prizes(id),
            FOREIGN KEY (prize_serial_id) REFERENCES prize_serials(id)
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_spin (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            line_user_id TEXT NOT NULL,
            date TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(line_user_id, date)
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS member_spin_limits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            line_user_id TEXT NOT NULL UNIQUE,
            daily_limit INTEGER,
            is_blocked INTEGER NOT NULL DEFAULT 0,
            note TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS prize_serials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prize_id INTEGER NOT NULL,
            serial_code TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'available',
            assigned_line_user_id TEXT,
            assigned_display_name TEXT,
            lottery_record_id INTEGER,
            assigned_at TEXT,
            checked_at TEXT,
            checked_by TEXT,
            source_order_no TEXT,
            source_sheet TEXT,
            source_row INTEGER,
            note TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (prize_id) REFERENCES prizes(id),
            FOREIGN KEY (lottery_record_id) REFERENCES lottery_records(id)
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS operation_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            level TEXT NOT NULL DEFAULT 'info',
            line_user_id TEXT,
            admin_line_user_id TEXT,
            message TEXT,
            payload_json TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_line_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            line_user_id TEXT NOT NULL UNIQUE,
            display_name TEXT,
            note TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )


def create_postgres_schema(db):
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS members (
            id SERIAL PRIMARY KEY,
            line_user_id TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL,
            picture_url TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS prizes (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            code TEXT NOT NULL UNIQUE,
            short_label TEXT,
            weight DOUBLE PRECISION NOT NULL,
            stock INTEGER,
            requires_serial INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS lottery_records (
            id SERIAL PRIMARY KEY,
            line_user_id TEXT NOT NULL,
            line_display_name TEXT,
            prize_id INTEGER,
            prize_serial_id INTEGER,
            prize_name TEXT NOT NULL,
            prize_code TEXT NOT NULL,
            serial_code TEXT,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_spin (
            id SERIAL PRIMARY KEY,
            line_user_id TEXT NOT NULL,
            date TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(line_user_id, date)
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS member_spin_limits (
            id SERIAL PRIMARY KEY,
            line_user_id TEXT NOT NULL UNIQUE,
            daily_limit INTEGER,
            is_blocked INTEGER NOT NULL DEFAULT 0,
            note TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS prize_serials (
            id SERIAL PRIMARY KEY,
            prize_id INTEGER NOT NULL,
            serial_code TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'available',
            assigned_line_user_id TEXT,
            assigned_display_name TEXT,
            lottery_record_id INTEGER,
            assigned_at TEXT,
            checked_at TEXT,
            checked_by TEXT,
            source_order_no TEXT,
            source_sheet TEXT,
            source_row INTEGER,
            note TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS operation_logs (
            id SERIAL PRIMARY KEY,
            event_type TEXT NOT NULL,
            level TEXT NOT NULL DEFAULT 'info',
            line_user_id TEXT,
            admin_line_user_id TEXT,
            message TEXT,
            payload_json TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_line_users (
            id SERIAL PRIMARY KEY,
            line_user_id TEXT NOT NULL UNIQUE,
            display_name TEXT,
            note TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )


def create_indexes(db):
    db.execute("CREATE INDEX IF NOT EXISTS idx_members_line_user_id ON members(line_user_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_lottery_records_user_created ON lottery_records(line_user_id, created_at)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_daily_spin_user_date ON daily_spin(line_user_id, date)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_prize_serials_prize_status ON prize_serials(prize_id, status)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_prize_serials_assigned_user ON prize_serials(assigned_line_user_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_operation_logs_created ON operation_logs(created_at)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_operation_logs_type_level ON operation_logs(event_type, level)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_admin_line_users_line_user_id ON admin_line_users(line_user_id)")


def ensure_common_columns(db):
    ensure_column(db, "prizes", "short_label", "TEXT")
    ensure_column(db, "prizes", "requires_serial", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(db, "lottery_records", "prize_serial_id", "INTEGER")
    ensure_column(db, "lottery_records", "serial_code", "TEXT")
    ensure_column(db, "lottery_records", "line_display_name", "TEXT")
    ensure_column(db, "prize_serials", "assigned_display_name", "TEXT")


def reset_sequences(db, table_names):
    safe_tables = [validate_identifier(table_name) for table_name in table_names]
    if db.is_sqlite:
        placeholders = ",".join("?" for _ in safe_tables)
        db.execute(f"DELETE FROM sqlite_sequence WHERE name IN ({placeholders})", safe_tables)
        return

    for table_name in safe_tables:
        db.execute(f"ALTER SEQUENCE IF EXISTS {table_name}_id_seq RESTART WITH 1")


def sync_sequences(db, table_names):
    if db.is_sqlite:
        return

    for table_name in [validate_identifier(table_name) for table_name in table_names]:
        db.execute(
            f"""
            SELECT setval(
                pg_get_serial_sequence('{table_name}', 'id'),
                COALESCE((SELECT MAX(id) FROM {table_name}), 0) + 1,
                false
            )
            """
        )


def database_health():
    try:
        with get_db() as db:
            db.execute("SELECT 1").fetchone()
        return True, None
    except Exception as error:
        LOGGER.exception("Database health check failed")
        return False, str(error)
