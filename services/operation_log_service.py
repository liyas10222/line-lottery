import json
import logging
from datetime import datetime

from config import Config
from services.database import get_db


LOGGER = logging.getLogger(__name__)


def now_iso():
    return datetime.now(Config.timezone()).isoformat(timespec="seconds")


def safe_json(payload):
    if payload is None:
        return None
    try:
        return json.dumps(payload, ensure_ascii=False, default=str)
    except TypeError:
        return json.dumps({"repr": repr(payload)}, ensure_ascii=False)


def write_operation_log(
    event_type,
    level="info",
    line_user_id=None,
    admin_line_user_id=None,
    message="",
    payload=None,
    db=None,
):
    timestamp = now_iso()
    payload_json = safe_json(payload)
    owns_connection = db is None

    try:
        if owns_connection:
            db = get_db()
        db.execute(
            """
            INSERT INTO operation_logs
                (event_type, level, line_user_id, admin_line_user_id, message, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_type,
                level,
                line_user_id,
                admin_line_user_id,
                message,
                payload_json,
                timestamp,
            ),
        )
        if owns_connection:
            db.commit()
    except Exception:
        LOGGER.exception("Failed to write operation log: %s", event_type)
        if owns_connection and db:
            try:
                db.rollback()
            except Exception:
                LOGGER.exception("Failed to rollback operation log write")
    finally:
        if owns_connection and db:
            try:
                db.close()
            except Exception:
                LOGGER.exception("Failed to close operation log connection")


def list_operation_logs(limit=100, event_type=None, level=None):
    try:
        limit = max(1, min(int(limit), 500))
    except (TypeError, ValueError):
        limit = 100

    where = []
    values = []
    if event_type:
        where.append("event_type = ?")
        values.append(event_type)
    if level:
        where.append("level = ?")
        values.append(level)

    where_sql = "WHERE " + " AND ".join(where) if where else ""
    with get_db() as db:
        rows = db.execute(
            f"""
            SELECT id, event_type, level, line_user_id, admin_line_user_id, message, payload_json, created_at
            FROM operation_logs
            {where_sql}
            ORDER BY id DESC
            LIMIT ?
            """,
            [*values, limit],
        ).fetchall()

    logs = []
    for row in rows:
        payload = None
        if row["payload_json"]:
            if len(row["payload_json"]) > 8000:
                payload = {"truncated": True, "bytes": len(row["payload_json"])}
            else:
                try:
                    payload = json.loads(row["payload_json"])
                except json.JSONDecodeError:
                    payload = row["payload_json"]
        logs.append(
            {
                "id": row["id"],
                "eventType": row["event_type"],
                "level": row["level"],
                "lineUserId": row["line_user_id"],
                "adminLineUserId": row["admin_line_user_id"],
                "message": row["message"],
                "payload": payload,
                "createdAt": row["created_at"],
            }
        )

    return {"ok": True, "logs": logs}, 200
