import logging
import random
from datetime import datetime

from config import Config
from services.database import create_schema, ensure_common_columns, get_db
from services.google_sheet_writer import mark_serial_assigned_in_sheet
from services.operation_log_service import write_operation_log


DEFAULT_PRIZES = []
LOGGER = logging.getLogger(__name__)

SERIAL_STATUSES = {"available", "assigned", "redeemed", "void"}


def now_local():
    return datetime.now(Config.timezone())


def now_iso():
    return now_local().isoformat(timespec="seconds")


def today_string():
    return now_local().date().isoformat()


def clean_text(value, max_length=255):
    if value is None:
        return ""
    return str(value).strip()[:max_length]


def as_bool(value):
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, int):
        return 1 if value else 0
    if isinstance(value, str):
        return 1 if value.strip().lower() in {"1", "true", "yes", "y", "on"} else 0
    return 0


def as_optional_int(value, minimum=None):
    if value is None or value == "":
        return None
    parsed = int(value)
    if minimum is not None and parsed < minimum:
        raise ValueError(f"must be >= {minimum}")
    return parsed


def as_optional_number(value, minimum=None):
    if value is None or value == "":
        return None
    parsed = float(value)
    if minimum is not None and parsed < minimum:
        raise ValueError(f"must be >= {minimum}")
    if parsed.is_integer():
        return int(parsed)
    return parsed


def validate_line_user_id(value):
    line_user_id = clean_text(value, 128)
    if not line_user_id:
        return None
    return line_user_id


def init_db():
    with get_db() as db:
        create_schema(db)
        ensure_common_columns(db)

        timestamp = now_iso()
        for name, code, short_label, weight, stock, active, requires_serial in DEFAULT_PRIZES:
            db.execute(
                """
                INSERT INTO prizes
                    (name, code, short_label, weight, stock, is_active, requires_serial, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(code) DO NOTHING
                """,
                (name, code, short_label, weight, stock, active, requires_serial, timestamp, timestamp),
            )

        db.execute(
            """
            INSERT INTO app_settings (key, value, created_at, updated_at)
            VALUES ('default_daily_spin_limit', ?, ?, ?)
            ON CONFLICT(key) DO NOTHING
            """,
            (str(Config.DEFAULT_DAILY_SPIN_LIMIT), timestamp, timestamp),
        )
        db.commit()


def get_setting(db, key, default_value):
    row = db.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default_value


def set_setting(key, value):
    timestamp = now_iso()
    with get_db() as db:
        db.execute(
            """
            INSERT INTO app_settings (key, value, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (key, str(value), timestamp, timestamp),
        )
        db.commit()
    return {"ok": True}, 200


def get_default_daily_limit(db):
    value = get_setting(db, "default_daily_spin_limit", str(Config.DEFAULT_DAILY_SPIN_LIMIT))
    try:
        return max(0, int(value))
    except ValueError:
        return max(0, Config.DEFAULT_DAILY_SPIN_LIMIT)


def sync_member(payload):
    line_user_id = validate_line_user_id(payload.get("lineUserId"))
    display_name = clean_text(payload.get("displayName"), 120)
    picture_url = clean_text(payload.get("pictureUrl"), 1000)

    if not line_user_id or not display_name:
        return {"ok": False, "message": "缺少 LINE 使用者資料"}, 400

    timestamp = now_iso()
    with get_db() as db:
        db.execute(
            """
            INSERT INTO members (line_user_id, display_name, picture_url, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(line_user_id) DO UPDATE SET
                display_name = excluded.display_name,
                picture_url = excluded.picture_url,
                updated_at = excluded.updated_at
            """,
            (line_user_id, display_name, picture_url, timestamp, timestamp),
        )
        db.commit()

    return {"ok": True}, 200


def get_member_quota(db, line_user_id):
    current_date = today_string()
    limit_row = db.execute(
        """
        SELECT daily_limit, is_blocked
        FROM member_spin_limits
        WHERE line_user_id = ?
        """,
        (line_user_id,),
    ).fetchone()
    spin_row = db.execute(
        """
        SELECT count
        FROM daily_spin
        WHERE line_user_id = ?
          AND date = ?
        """,
        (line_user_id, current_date),
    ).fetchone()

    default_limit = get_default_daily_limit(db)
    daily_limit = limit_row["daily_limit"] if limit_row and limit_row["daily_limit"] is not None else default_limit
    is_blocked = bool(limit_row["is_blocked"]) if limit_row else False
    used_count = spin_row["count"] if spin_row else 0
    remaining = 0 if is_blocked else max(0, daily_limit - used_count)

    return {
        "date": current_date,
        "dailyLimit": daily_limit,
        "used": used_count,
        "remaining": remaining,
        "canSpin": remaining > 0,
        "isBlocked": is_blocked,
    }


def get_lottery_status(line_user_id):
    line_user_id = validate_line_user_id(line_user_id)
    if not line_user_id:
        return {"ok": False, "message": "缺少 lineUserId"}, 400

    with get_db() as db:
        quota = get_member_quota(db, line_user_id)

    return {
        "ok": True,
        "canSpin": quota["canSpin"],
        "remaining": quota["remaining"],
        "dailyLimit": quota["dailyLimit"],
        "used": quota["used"],
        "isBlocked": quota["isBlocked"],
    }, 200


def fetch_prizes_with_serial_count(db):
    return db.execute(
        """
        SELECT
            p.id,
            p.name,
            p.code,
            p.short_label,
            p.weight,
            p.stock,
            p.requires_serial,
            p.is_active,
            COALESCE((
                SELECT COUNT(*)
                FROM prize_serials ps
                WHERE ps.prize_id = p.id
                  AND ps.status = 'available'
            ), 0) AS available_serials
        FROM prizes p
        ORDER BY p.id ASC
        """
    ).fetchall()


def is_prize_eligible(prize):
    has_stock = prize["stock"] is None or prize["stock"] > 0
    has_serial = not prize["requires_serial"] or prize["available_serials"] > 0
    return bool(prize["is_active"]) and prize["weight"] > 0 and has_stock and has_serial


def serialize_prizes(prizes, include_inactive=True):
    eligible_prizes = [prize for prize in prizes if is_prize_eligible(prize)]
    total_weight = sum(prize["weight"] for prize in eligible_prizes)
    output = []

    for prize in prizes:
        if not include_inactive and not bool(prize["is_active"]):
            continue

        eligible = is_prize_eligible(prize)
        probability = (prize["weight"] / total_weight) if eligible and total_weight > 0 else 0
        output.append(
            {
                "id": prize["id"],
                "name": prize["name"],
                "code": prize["code"],
                "shortLabel": prize["short_label"] or prize["name"],
                "weight": prize["weight"],
                "stock": prize["stock"],
                "requiresSerial": bool(prize["requires_serial"]),
                "isActive": bool(prize["is_active"]),
                "availableSerials": prize["available_serials"],
                "isEligible": eligible,
                "probability": round(probability, 6),
                "probabilityPercent": round(probability * 100, 2),
            }
        )
    return output


def get_public_prizes():
    with get_db() as db:
        prizes = fetch_prizes_with_serial_count(db)
    return {"ok": True, "prizes": serialize_prizes(prizes, include_inactive=False)}, 200


def get_admin_prizes():
    with get_db() as db:
        prizes = fetch_prizes_with_serial_count(db)
    return {"ok": True, "prizes": serialize_prizes(prizes, include_inactive=True)}, 200


def get_available_prizes(db):
    return [prize for prize in fetch_prizes_with_serial_count(db) if is_prize_eligible(prize)]


def choose_prize(prizes):
    total_weight = sum(prize["weight"] for prize in prizes)
    if total_weight <= 0:
        return None

    pick = random.SystemRandom().uniform(0, total_weight)
    cursor = 0
    for prize in prizes:
        cursor += prize["weight"]
        if pick <= cursor:
            return prize
    return prizes[-1]


def reserve_prize_serial(db, prize, line_user_id, line_display_name, timestamp):
    if not prize["requires_serial"]:
        return None

    sql = """
        SELECT id, serial_code, source_sheet, source_row
        FROM prize_serials
        WHERE prize_id = ?
          AND status = 'available'
        ORDER BY id ASC
        LIMIT 1
        """
    if db.is_postgres:
        sql = """
        SELECT id, serial_code, source_sheet, source_row
        FROM prize_serials
        WHERE prize_id = ?
          AND status = 'available'
        ORDER BY id ASC
        LIMIT 1
        FOR UPDATE SKIP LOCKED
        """

    serial = db.execute(sql, (prize["id"],)).fetchone()
    if serial is None:
        return None

    db.execute(
        """
        UPDATE prize_serials
        SET status = 'assigned',
            assigned_line_user_id = ?,
            assigned_display_name = ?,
            assigned_at = ?,
            updated_at = ?
        WHERE id = ?
          AND status = 'available'
        """,
        (line_user_id, line_display_name, timestamp, timestamp, serial["id"]),
    )
    return serial


def spin_lottery(payload):
    line_user_id = validate_line_user_id(payload.get("lineUserId"))
    payload_display_name = clean_text(payload.get("displayName") or payload.get("lineDisplayName"), 120)
    if not line_user_id:
        return {"ok": False, "message": "缺少 lineUserId"}, 400

    db = get_db()
    sheet_writeback = None
    try:
        db.begin_immediate()

        timestamp = now_iso()
        member = db.execute(
            "SELECT display_name FROM members WHERE line_user_id = ?",
            (line_user_id,),
        ).fetchone()
        line_display_name = clean_text(member["display_name"], 120) if member else payload_display_name
        if not line_display_name:
            line_display_name = payload_display_name
        quota = get_member_quota(db, line_user_id)
        if quota["isBlocked"]:
            db.rollback()
            return {"ok": False, "message": "此會員目前無法抽獎"}, 200
        if quota["remaining"] <= 0:
            db.rollback()
            return {"ok": False, "message": "今日已抽過"}, 200

        prizes = get_available_prizes(db)
        if not prizes:
            db.rollback()
            return {"ok": False, "message": "目前沒有可用獎項"}, 503

        prize = choose_prize(prizes)
        if prize is None:
            db.rollback()
            return {"ok": False, "message": "目前沒有可用獎項"}, 503

        serial = reserve_prize_serial(db, prize, line_user_id, line_display_name, timestamp)
        if prize["requires_serial"] and serial is None:
            db.rollback()
            return {"ok": False, "message": "此獎項序號已用完"}, 409

        status = "not_won" if prize["code"] == "NONE" else "won"
        cursor = db.execute_insert(
            """
            INSERT INTO lottery_records
                (
                    line_user_id,
                    line_display_name,
                    prize_id,
                    prize_serial_id,
                    prize_name,
                    prize_code,
                    serial_code,
                    status,
                    created_at
                )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                line_user_id,
                line_display_name,
                prize["id"],
                serial["id"] if serial else None,
                prize["name"],
                prize["code"],
                serial["serial_code"] if serial else None,
                status,
                timestamp,
            ),
        )
        record_id = cursor.lastrowid

        if serial:
            db.execute(
                """
                UPDATE prize_serials
                SET lottery_record_id = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (record_id, timestamp, serial["id"]),
            )
            sheet_writeback = {
                "id": serial["id"],
                "serial_code": serial["serial_code"],
                "source_sheet": serial["source_sheet"],
                "source_row": serial["source_row"],
                "line_user_id": line_user_id,
                "line_display_name": line_display_name,
                "lottery_record_id": record_id,
                "assigned_at": timestamp,
            }

        db.execute(
            """
            INSERT INTO daily_spin (line_user_id, date, count, created_at, updated_at)
            VALUES (?, ?, 1, ?, ?)
            ON CONFLICT(line_user_id, date) DO UPDATE SET
                count = daily_spin.count + 1,
                updated_at = excluded.updated_at
            """,
            (line_user_id, quota["date"], timestamp, timestamp),
        )

        if prize["stock"] is not None:
            db.execute(
                "UPDATE prizes SET stock = stock - 1, updated_at = ? WHERE id = ? AND stock > 0",
                (timestamp, prize["id"]),
            )

        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    write_operation_log(
        "lottery_spin_success",
        line_user_id=line_user_id,
        message="Lottery spin committed",
        payload={
            "recordId": record_id,
            "prizeId": prize["id"],
            "prizeCode": prize["code"],
            "prizeName": prize["name"],
            "serialCode": serial["serial_code"] if serial else None,
            "status": status,
        },
    )

    sheet_writeback_result = {"ok": True, "skipped": True, "message": "No serial writeback required"}
    if sheet_writeback:
        try:
            sheet_writeback_result = mark_serial_assigned_in_sheet(
                sheet_writeback,
                sheet_writeback["line_user_id"],
                sheet_writeback["line_display_name"],
                sheet_writeback["lottery_record_id"],
                sheet_writeback["assigned_at"],
            )
        except Exception as error:
            LOGGER.exception("Google Sheet writeback failed after committed spin")
            sheet_writeback_result = {"ok": False, "message": str(error)}

        write_operation_log(
            "google_sheet_writeback",
            level="info" if sheet_writeback_result.get("ok") else "error",
            line_user_id=line_user_id,
            message="Google Sheet writeback completed"
            if sheet_writeback_result.get("ok")
            else "Google Sheet writeback failed",
            payload={
                "recordId": record_id,
                "serialCode": sheet_writeback.get("serial_code"),
                "result": sheet_writeback_result,
            },
        )

    return {
        "ok": True,
        "prize": {
            "id": prize["id"],
            "name": prize["name"],
            "code": prize["code"],
            "serialCode": serial["serial_code"] if serial else None,
            "status": status,
        },
        "sheetWriteback": sheet_writeback_result,
    }, 200


def get_history(line_user_id):
    line_user_id = validate_line_user_id(line_user_id)
    if not line_user_id:
        return {"ok": False, "message": "缺少 lineUserId"}, 400

    with get_db() as db:
        rows = db.execute(
            """
            SELECT prize_name, prize_code, serial_code, status, created_at
            FROM lottery_records
            WHERE line_user_id = ?
            ORDER BY created_at DESC, id DESC
            """,
            (line_user_id,),
        ).fetchall()

    records = [
        {
            "prizeName": row["prize_name"],
            "prizeCode": row["prize_code"],
            "serialCode": row["serial_code"],
            "status": row["status"],
            "createdAt": row["created_at"],
        }
        for row in rows
    ]
    return {"ok": True, "records": records}, 200


def update_prize(prize_id, payload):
    fields = []
    values = []

    if "name" in payload:
        name = clean_text(payload["name"], 120)
        if not name:
            return {"ok": False, "message": "獎項名稱不可空白"}, 400
        fields.append("name = ?")
        values.append(name)
    if "code" in payload:
        fields.append("code = ?")
        values.append(clean_text(payload["code"], 80))
    if "shortLabel" in payload:
        fields.append("short_label = ?")
        values.append(clean_text(payload["shortLabel"], 24))
    if "weight" in payload:
        fields.append("weight = ?")
        values.append(as_optional_number(payload["weight"], minimum=0))
    if "stock" in payload:
        fields.append("stock = ?")
        values.append(as_optional_int(payload["stock"], minimum=0))
    if "requiresSerial" in payload:
        fields.append("requires_serial = ?")
        values.append(as_bool(payload["requiresSerial"]))
    if "isActive" in payload:
        fields.append("is_active = ?")
        values.append(as_bool(payload["isActive"]))

    if not fields:
        return {"ok": False, "message": "沒有可更新欄位"}, 400

    fields.append("updated_at = ?")
    values.append(now_iso())
    values.append(prize_id)

    with get_db() as db:
        cursor = db.execute(f"UPDATE prizes SET {', '.join(fields)} WHERE id = ?", values)
        db.commit()
        if cursor.rowcount == 0:
            return {"ok": False, "message": "找不到獎項"}, 404

    return {"ok": True}, 200


def set_global_daily_limit(payload):
    try:
        daily_limit = as_optional_int(payload.get("dailyLimit"), minimum=0)
    except (TypeError, ValueError):
        return {"ok": False, "message": "dailyLimit 必須是 0 以上整數"}, 400
    if daily_limit is None:
        return {"ok": False, "message": "缺少 dailyLimit"}, 400
    return set_setting("default_daily_spin_limit", daily_limit)


def set_member_spin_limit(line_user_id, payload):
    line_user_id = validate_line_user_id(line_user_id)
    if not line_user_id:
        return {"ok": False, "message": "缺少 lineUserId"}, 400

    try:
        daily_limit = as_optional_int(payload.get("dailyLimit"), minimum=0)
    except (TypeError, ValueError):
        return {"ok": False, "message": "dailyLimit 必須是 0 以上整數或 null"}, 400

    is_blocked = as_bool(payload.get("isBlocked", False))
    note = clean_text(payload.get("note"), 500)
    timestamp = now_iso()

    with get_db() as db:
        db.execute(
            """
            INSERT INTO member_spin_limits
                (line_user_id, daily_limit, is_blocked, note, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(line_user_id) DO UPDATE SET
                daily_limit = excluded.daily_limit,
                is_blocked = excluded.is_blocked,
                note = excluded.note,
                updated_at = excluded.updated_at
            """,
            (line_user_id, daily_limit, is_blocked, note, timestamp, timestamp),
        )
        quota = get_member_quota(db, line_user_id)
        db.commit()

    return {"ok": True, "quota": quota}, 200


def get_member_spin_limit(line_user_id):
    line_user_id = validate_line_user_id(line_user_id)
    if not line_user_id:
        return {"ok": False, "message": "缺少 lineUserId"}, 400
    with get_db() as db:
        quota = get_member_quota(db, line_user_id)
    return {"ok": True, "quota": quota}, 200


def reset_member_daily_spin(line_user_id, target_date=None):
    line_user_id = validate_line_user_id(line_user_id)
    if not line_user_id:
        return {"ok": False, "message": "缺少 lineUserId"}, 400

    spin_date = clean_text(target_date, 20) if target_date else today_string()
    with get_db() as db:
        db.execute(
            """
            DELETE FROM daily_spin
            WHERE line_user_id = ?
              AND date = ?
            """,
            (line_user_id, spin_date),
        )
        quota = get_member_quota(db, line_user_id)
        db.commit()

    return {"ok": True, "date": spin_date, "quota": quota}, 200


def normalize_serial_entries(payload):
    entries = []
    if isinstance(payload.get("serialCodes"), list):
        for code in payload["serialCodes"]:
            entries.append({"serialCode": code})
    if isinstance(payload.get("serials"), list):
        for item in payload["serials"]:
            if isinstance(item, dict):
                entries.append(item)
            else:
                entries.append({"serialCode": item})
    return entries


def add_prize_serials(prize_id, payload):
    entries = normalize_serial_entries(payload)
    if not entries:
        return {"ok": False, "message": "缺少 serialCodes 或 serials"}, 400

    timestamp = now_iso()
    created = []
    skipped = []
    with get_db() as db:
        prize = db.execute("SELECT id FROM prizes WHERE id = ?", (prize_id,)).fetchone()
        if not prize:
            return {"ok": False, "message": "找不到獎項"}, 404

        for item in entries:
            serial_code = clean_text(item.get("serialCode"), 120)
            if not serial_code:
                skipped.append({"serialCode": serial_code, "reason": "empty"})
                continue

            cursor = db.execute(
                """
                INSERT OR IGNORE INTO prize_serials
                    (prize_id, serial_code, status, source_order_no, source_sheet, source_row, note, created_at, updated_at)
                VALUES (?, ?, 'available', ?, ?, ?, ?, ?, ?)
                """,
                (
                    prize_id,
                    serial_code,
                    clean_text(item.get("sourceOrderNo") or payload.get("sourceOrderNo"), 80),
                    clean_text(item.get("sourceSheet") or payload.get("sourceSheet"), 80),
                    as_optional_int(item.get("sourceRow"), minimum=1) if item.get("sourceRow") is not None else None,
                    clean_text(item.get("note") or payload.get("note"), 500),
                    timestamp,
                    timestamp,
                ),
            )
            if cursor.rowcount:
                created.append(serial_code)
            else:
                skipped.append({"serialCode": serial_code, "reason": "duplicate"})

        db.commit()

    return {"ok": True, "created": created, "skipped": skipped}, 200


def list_prize_serials(filters):
    where = []
    values = []

    prize_id = filters.get("prizeId")
    if prize_id:
        where.append("ps.prize_id = ?")
        values.append(int(prize_id))

    status = clean_text(filters.get("status"), 30)
    if status:
        where.append("ps.status = ?")
        values.append(status)

    where_sql = "WHERE " + " AND ".join(where) if where else ""
    with get_db() as db:
        rows = db.execute(
            f"""
            SELECT
                ps.id,
                ps.prize_id,
                p.name AS prize_name,
                ps.serial_code,
                ps.status,
                ps.assigned_line_user_id,
                ps.assigned_display_name,
                ps.lottery_record_id,
                ps.assigned_at,
                ps.checked_at,
                ps.checked_by,
                ps.source_order_no,
                ps.source_sheet,
                ps.source_row,
                ps.note,
                ps.created_at,
                ps.updated_at
            FROM prize_serials ps
            JOIN prizes p ON p.id = ps.prize_id
            {where_sql}
            ORDER BY ps.id DESC
            LIMIT 500
            """,
            values,
        ).fetchall()

    serials = [
        {
            "id": row["id"],
            "prizeId": row["prize_id"],
            "prizeName": row["prize_name"],
            "serialCode": row["serial_code"],
            "status": row["status"],
            "assignedLineUserId": row["assigned_line_user_id"],
            "assignedDisplayName": row["assigned_display_name"],
            "lotteryRecordId": row["lottery_record_id"],
            "assignedAt": row["assigned_at"],
            "checkedAt": row["checked_at"],
            "checkedBy": row["checked_by"],
            "sourceOrderNo": row["source_order_no"],
            "sourceSheet": row["source_sheet"],
            "sourceRow": row["source_row"],
            "note": row["note"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }
        for row in rows
    ]
    return {"ok": True, "serials": serials}, 200


def update_prize_serial(serial_id, payload):
    status = clean_text(payload.get("status"), 30)
    if status and status not in SERIAL_STATUSES:
        return {"ok": False, "message": "序號狀態不正確"}, 400

    fields = []
    values = []
    timestamp = now_iso()

    if status:
        fields.append("status = ?")
        values.append(status)
        if status == "redeemed":
            fields.append("checked_at = ?")
            values.append(timestamp)
            fields.append("checked_by = ?")
            values.append(clean_text(payload.get("checkedBy"), 80) or "admin")

    if "note" in payload:
        fields.append("note = ?")
        values.append(clean_text(payload.get("note"), 500))

    if not fields:
        return {"ok": False, "message": "沒有可更新欄位"}, 400

    fields.append("updated_at = ?")
    values.append(timestamp)
    values.append(serial_id)

    with get_db() as db:
        cursor = db.execute(f"UPDATE prize_serials SET {', '.join(fields)} WHERE id = ?", values)
        db.commit()
        if cursor.rowcount == 0:
            return {"ok": False, "message": "找不到序號"}, 404

    return {"ok": True}, 200
