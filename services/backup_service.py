from datetime import datetime

from config import Config
from services.database import get_db, sync_sequences, table_columns, validate_identifier
from services.operation_log_service import write_operation_log


BACKUP_TABLES = [
    "members",
    "lottery_records",
    "daily_spin",
    "prizes",
    "prize_serials",
    "member_spin_limits",
    "app_settings",
    "operation_logs",
]


def now_iso():
    return datetime.now(Config.timezone()).isoformat(timespec="seconds")


def row_to_dict(row):
    return dict(row)


def export_backup():
    backup = {
        "ok": True,
        "version": 1,
        "exportedAt": now_iso(),
        "tables": {},
    }

    with get_db() as db:
        backup["databaseMode"] = db.dialect
        for table_name in BACKUP_TABLES:
            validate_identifier(table_name)
            columns = table_columns(db, table_name)
            order_column = "id" if "id" in columns else "key" if "key" in columns else None
            order_sql = f" ORDER BY {order_column} ASC" if order_column else ""
            rows = db.execute(f"SELECT * FROM {table_name}{order_sql}").fetchall()
            backup["tables"][table_name] = [row_to_dict(row) for row in rows]

    write_operation_log(
        "backup_export",
        message="Backup exported",
        payload={table: len(rows) for table, rows in backup["tables"].items()},
    )
    return backup, 200


def normalize_backup_payload(payload):
    if isinstance(payload, dict) and "backup" in payload and isinstance(payload["backup"], dict):
        return payload["backup"]
    return payload


def import_backup(payload):
    backup = normalize_backup_payload(payload)
    if not isinstance(backup, dict) or not isinstance(backup.get("tables"), dict):
        return {"ok": False, "message": "Invalid backup payload"}, 400

    imported = {}
    skipped_tables = []

    with get_db() as db:
        if db.is_sqlite:
            db.execute("PRAGMA foreign_keys = OFF")
        db.begin_immediate()
        try:
            for table_name in BACKUP_TABLES:
                rows = backup["tables"].get(table_name)
                if rows is None:
                    skipped_tables.append(table_name)
                    continue
                if not isinstance(rows, list):
                    raise ValueError(f"Backup table {table_name} must be a list")

                existing_columns = table_columns(db, table_name)
                inserted_count = 0
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    columns = [column for column in row.keys() if column in existing_columns]
                    if not columns:
                        continue
                    for column in columns:
                        validate_identifier(column)
                    column_sql = ", ".join(columns)
                    placeholders = ", ".join("?" for _ in columns)
                    sql = f"INSERT OR IGNORE INTO {table_name} ({column_sql}) VALUES ({placeholders})"
                    cursor = db.execute(sql, [row[column] for column in columns])
                    if cursor.rowcount:
                        inserted_count += cursor.rowcount
                imported[table_name] = inserted_count

            sync_sequences(db, [table for table in BACKUP_TABLES if table != "app_settings"])
            db.commit()
        except Exception:
            db.rollback()
            raise

    write_operation_log(
        "backup_import",
        message="Backup imported",
        payload={"imported": imported, "skippedTables": skipped_tables},
    )
    return {"ok": True, "imported": imported, "skippedTables": skipped_tables}, 200
