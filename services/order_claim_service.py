import json
import re
import hashlib
import unicodedata
import urllib.parse
from decimal import Decimal, InvalidOperation

from config import Config
from services.database import get_db
from services.google_sheet_service import (
    SHEETS_SCOPE,
    get_authorized_session,
    request_google_json,
    send_google_json,
)
from services.lottery_service import (
    acquire_member_spin_lock,
    clean_text,
    get_default_daily_limit,
    get_member_quota,
    now_iso,
    validate_line_user_id,
)
from services.operation_log_service import write_operation_log


GENERIC_NOT_FOUND_MESSAGE = "查無此訂單，請聯繫客服人員作協助確認"
ORDER_CLAIM_RANGE_COLUMNS = "A:S"
SYSTEM_STORE_PAYMENT = "系統超商"


def normalize_lookup(value):
    text = unicodedata.normalize("NFKC", str(value or "")).strip().lower()
    return re.sub(r"\s+", "", text)


def parse_decimal(value):
    text = clean_text(value, 80).replace(",", "")
    match = re.search(r"\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return Decimal(match.group(0))
    except InvalidOperation:
        return None


def decimal_key(value):
    number = parse_decimal(value)
    if number is None:
        return ""
    normalized = number.normalize()
    return format(normalized, "f")


def order_source_key(order):
    payment_method = normalize_lookup(order.get("paymentMethod"))
    payment_no = normalize_lookup(order.get("paymentNo"))
    if payment_method == normalize_lookup(SYSTEM_STORE_PAYMENT) and payment_no:
        raw_key = "|".join(
            [
                "store",
                f"payment_no:{payment_no}",
                f"uid:{normalize_lookup(order.get('uid'))}",
                f"role:{normalize_lookup(order.get('role'))}",
            ]
        )
        return f"order:{hashlib.sha256(raw_key.encode('utf-8')).hexdigest()}"

    raw_key = "|".join(
        [
            "remittance",
            f"order_info:{normalize_lookup(order.get('orderInfo'))}",
            f"amount:{decimal_key(order.get('amount'))}",
            f"payment_method:{payment_method}",
            f"points:{parse_positive_int(order.get('pointsRaw')) or order.get('points') or ''}",
        ]
    )
    return f"order:{hashlib.sha256(raw_key.encode('utf-8')).hexdigest()}"


def parse_positive_int(value):
    number = parse_decimal(value)
    if number is None:
        return None
    try:
        parsed = int(number)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def is_false_checkbox(value):
    text = normalize_lookup(value)
    return text in {"", "false", "0", "否", "no", "n", "off"}


def extract_order_field(order_info, labels):
    text = str(order_info or "")
    for label in labels:
        match = re.search(rf"{re.escape(label)}\s*[:：]\s*([^\n\r]+)", text, re.IGNORECASE)
        if match:
            value = match.group(1).strip()
            next_label_pattern = r"\s*/?\s*(?:UID|uid|角色名字|人物名稱|角色名稱|名稱)\s*[:：]"
            return re.split(next_label_pattern, value, maxsplit=1)[0].strip(" /")
    return ""


def resolve_order_claim_sheet(session):
    if not Config.GOOGLE_SHEET_ID:
        return None, "未設定 GOOGLE_SHEET_ID"

    url = (
        f"https://sheets.googleapis.com/v4/spreadsheets/{Config.GOOGLE_SHEET_ID}"
        "?fields=sheets.properties(sheetId,title,index,gridProperties)"
    )
    data, error = request_google_json(session, url)
    if error:
        return None, error

    sheets = data.get("sheets", [])
    if not sheets:
        return None, "Google Sheet 沒有可讀取的分頁"

    target_gid = clean_text(getattr(Config, "ORDER_CLAIM_SHEET_GID", ""), 80)
    target_name = clean_text(getattr(Config, "ORDER_CLAIM_SHEET_NAME", ""), 120)

    if target_gid:
        for sheet in sheets:
            props = sheet.get("properties", {})
            if str(props.get("sheetId")) == target_gid:
                return props, None

    if target_name:
        for sheet in sheets:
            props = sheet.get("properties", {})
            if props.get("title") == target_name:
                return props, None

    return None, "找不到訂單領取分頁"


def fetch_order_claim_rows(session):
    sheet_props, error = resolve_order_claim_sheet(session)
    if error:
        return None, error

    sheet_title = sheet_props.get("title")
    row_count = sheet_props.get("gridProperties", {}).get("rowCount") or 1000
    quoted_title = sheet_title.replace("'", "''")
    range_name = f"'{quoted_title}'!A1:S{max(2, row_count)}"
    encoded_range = urllib.parse.quote(range_name, safe="")
    url = (
        f"https://sheets.googleapis.com/v4/spreadsheets/{Config.GOOGLE_SHEET_ID}"
        f"/values/{encoded_range}?valueRenderOption=FORMATTED_VALUE"
    )
    data, error = request_google_json(session, url)
    if error:
        return None, error

    values = data.get("values", [])
    orders = []
    for offset, row in enumerate(values[1:], start=2):
        order_info = cell(row, 2)
        amount = cell(row, 5)
        payment_method = cell(row, 7)
        payment_no = cell(row, 13)
        issued_raw = cell(row, 16)
        points_raw = cell(row, 17)
        points = parse_positive_int(points_raw)
        uid = extract_order_field(order_info, ["UID", "uid"])
        role = extract_order_field(order_info, ["角色名字", "人物名稱", "角色名稱", "名稱"])

        if not any([order_info, amount, payment_method, payment_no, issued_raw, points_raw]):
            continue

        orders.append(
            {
                "rowNumber": offset,
                "orderInfo": order_info,
                "amount": amount,
                "paymentMethod": payment_method,
                "paymentNo": payment_no,
                "issuedRaw": issued_raw,
                "isIssued": not is_false_checkbox(issued_raw),
                "pointsRaw": points_raw,
                "points": points,
                "uid": uid,
                "role": role,
                "sourceKey": None,
            }
        )

    for order in orders:
        order["sourceKey"] = order_source_key(order)

    return {"sheetTitle": sheet_title, "orders": orders}, None


def cell(row, index):
    if index >= len(row):
        return ""
    return clean_text(row[index], 5000)


def eligible_order(order):
    return not order["isIssued"] and order["points"] is not None and order["points"] > 0


def match_store_order(orders, payment_no, lookup_value):
    normalized_payment_no = normalize_lookup(payment_no)
    normalized_lookup = normalize_lookup(lookup_value)
    if not normalized_payment_no or not normalized_lookup:
        return []
    candidates = [
        order
        for order in orders
        if eligible_order(order)
        and normalize_lookup(order["paymentMethod"]) == normalize_lookup(SYSTEM_STORE_PAYMENT)
        and normalize_lookup(order["paymentNo"]) == normalized_payment_no
    ]

    uid_matches = [order for order in candidates if normalize_lookup(order["uid"]) == normalized_lookup]
    if uid_matches:
        return uid_matches

    return [order for order in candidates if normalize_lookup(order["role"]) == normalized_lookup]


def match_remittance_order(orders, lookup_value, amount):
    normalized_lookup = normalize_lookup(lookup_value)
    amount_key = decimal_key(amount)
    if not normalized_lookup or not amount_key:
        return []

    candidates = [
        order
        for order in orders
        if eligible_order(order)
        and normalize_lookup(order["paymentMethod"]) != normalize_lookup(SYSTEM_STORE_PAYMENT)
        and decimal_key(order["amount"]) == amount_key
    ]

    uid_matches = [order for order in candidates if normalize_lookup(order["uid"]) == normalized_lookup]
    if uid_matches:
        return uid_matches

    return [order for order in candidates if normalize_lookup(order["role"]) == normalized_lookup]


def select_claim_match(payload, orders):
    claim_type = clean_text(payload.get("claimType"), 40)
    if claim_type == "store":
        payment_no = clean_text(payload.get("paymentNo"), 120)
        lookup_value = clean_text(payload.get("lookupValue"), 160)
        return match_store_order(orders, payment_no, lookup_value), {
            "claimType": "store",
            "matchType": "payment_no_and_uid_or_name",
            "paymentNo": payment_no,
            "lookupValue": lookup_value,
        }

    if claim_type == "remittance":
        lookup_value = clean_text(payload.get("lookupValue"), 160)
        amount = clean_text(payload.get("amount"), 80)
        return match_remittance_order(orders, lookup_value, amount), {
            "claimType": "remittance",
            "matchType": "uid_or_name_amount",
            "lookupValue": lookup_value,
            "amount": amount,
        }

    return [], {"claimType": claim_type or "unknown", "matchType": "invalid"}


def claim_order_spins(payload):
    line_user_id = validate_line_user_id(payload.get("lineUserId"))
    display_name = clean_text(payload.get("displayName"), 120) or "LINE會員"
    if not line_user_id:
        return {"ok": False, "message": "請先使用 LINE 登入後再領取抽獎次數"}, 400

    session, error = get_authorized_session([SHEETS_SCOPE])
    if error:
        write_operation_log(
            "order_claim_sheet_read",
            level="error",
            line_user_id=line_user_id,
            message="Order claim sheet read failed",
            payload={"error": error},
        )
        return {"ok": False, "message": "訂單資料暫時無法讀取，請稍後再試"}, 503

    sheet_data, error = fetch_order_claim_rows(session)
    if error:
        write_operation_log(
            "order_claim_sheet_read",
            level="error",
            line_user_id=line_user_id,
            message="Order claim sheet read failed",
            payload={"error": error},
        )
        return {"ok": False, "message": "訂單資料暫時無法讀取，請稍後再試"}, 503

    matches, criteria = select_claim_match(payload, sheet_data["orders"])
    if len(matches) != 1:
        write_operation_log(
            "order_claim_match_rejected",
            level="warning",
            line_user_id=line_user_id,
            message="Order claim rejected because match count is not exactly one",
            payload={"criteria": safe_criteria(criteria), "matchCount": len(matches)},
        )
        return {"ok": False, "message": GENERIC_NOT_FOUND_MESSAGE}, 200

    order = matches[0]
    timestamp = now_iso()
    sheet_title = sheet_data["sheetTitle"]
    source_key = order["sourceKey"]
    input_payload = build_input_payload(criteria, order, payload)

    with get_db() as db:
        try:
            db.begin_immediate()
            acquire_member_spin_lock(db, line_user_id)
            quota = get_member_quota(db, line_user_id)
            if quota["isBlocked"]:
                db.rollback()
                return {"ok": False, "message": "此會員目前無法領取抽獎次數"}, 403

            db.execute(
                """
                INSERT INTO members (line_user_id, display_name, picture_url, created_at, updated_at)
                VALUES (?, ?, '', ?, ?)
                ON CONFLICT(line_user_id) DO UPDATE SET
                    display_name = excluded.display_name,
                    updated_at = excluded.updated_at
                """,
                (line_user_id, display_name, timestamp, timestamp),
            )

            insert_cursor = db.execute(
                """
                INSERT INTO order_claim_records
                    (
                        line_user_id,
                        line_display_name,
                        claim_type,
                        lookup_value,
                        amount,
                        payment_no,
                        points,
                        source_key,
                        source_sheet,
                        source_row,
                        payment_method,
                        status,
                        input_payload_json,
                        created_at,
                        updated_at
                    )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'claimed', ?, ?, ?)
                ON CONFLICT(source_key) DO NOTHING
                """,
                (
                    line_user_id,
                    display_name,
                    input_payload["claimType"],
                    input_payload.get("lookupValue"),
                    input_payload.get("amount"),
                    input_payload.get("paymentNo"),
                    order["points"],
                    source_key,
                    sheet_title,
                    order["rowNumber"],
                    order["paymentMethod"],
                    json.dumps(input_payload, ensure_ascii=False),
                    timestamp,
                    timestamp,
                ),
            )
            if insert_cursor.rowcount == 0:
                db.rollback()
                write_operation_log(
                    "order_claim_duplicate_rejected",
                    level="warning",
                    line_user_id=line_user_id,
                    message="Order claim rejected because source key was already claimed",
                    payload={"sheet": sheet_title, "rowNumber": order["rowNumber"], "sourceKey": source_key},
                )
                return {"ok": False, "message": GENERIC_NOT_FOUND_MESSAGE}, 200

            current_limit = quota["dailyLimit"] if quota["dailyLimit"] is not None else get_default_daily_limit(db)
            next_limit = int(current_limit) + int(order["points"])
            db.execute(
                """
                INSERT INTO member_spin_limits
                    (line_user_id, daily_limit, is_blocked, note, created_at, updated_at)
                VALUES (?, ?, 0, '', ?, ?)
                ON CONFLICT(line_user_id) DO UPDATE SET
                    daily_limit = ?,
                    updated_at = ?
                """,
                (line_user_id, next_limit, timestamp, timestamp, next_limit, timestamp),
            )
            updated_quota = get_member_quota(db, line_user_id)
            db.commit()
        except Exception:
            db.rollback()
            raise

    sheet_writeback = mark_order_claim_issued(session, sheet_title, order["rowNumber"])
    record_sheet_writeback(line_user_id, source_key, sheet_title, order["rowNumber"], sheet_writeback)

    result = {
        "ok": True,
        "message": f"已成功領取 {order['points']} 次抽獎次數",
        "points": order["points"],
        "remaining": updated_quota["remaining"],
        "quota": updated_quota,
        "sheetWriteback": sheet_writeback,
    }
    write_operation_log(
        "order_claim_success",
        level="info" if sheet_writeback.get("ok") else "warning",
        line_user_id=line_user_id,
        message="Order claim succeeded",
        payload={
            "points": order["points"],
            "sheet": sheet_title,
            "rowNumber": order["rowNumber"],
            "sourceKey": source_key,
            "claimType": input_payload["claimType"],
            "sheetWriteback": sheet_writeback,
        },
    )
    return result, 200


def build_input_payload(criteria, order, payload):
    return {
        "claimType": criteria.get("claimType"),
        "matchType": criteria.get("matchType"),
        "lookupValue": clean_text(criteria.get("lookupValue"), 160),
        "amount": clean_text(criteria.get("amount") or order.get("amount"), 80),
        "paymentNo": clean_text(criteria.get("paymentNo"), 120),
        "paymentMethod": order.get("paymentMethod"),
        "sourceRow": order.get("rowNumber"),
        "sourceKey": order.get("sourceKey"),
        "lineUserId": validate_line_user_id(payload.get("lineUserId")),
    }


def mark_order_claim_issued(session, sheet_title, row_number):
    quoted_title = sheet_title.replace("'", "''")
    range_name = f"'{quoted_title}'!Q{row_number}"
    encoded_range = urllib.parse.quote(range_name, safe="")
    url = (
        f"https://sheets.googleapis.com/v4/spreadsheets/{Config.GOOGLE_SHEET_ID}"
        f"/values/{encoded_range}?valueInputOption=USER_ENTERED"
    )
    payload = {"range": range_name, "majorDimension": "ROWS", "values": [[True]]}
    data, error = send_google_json(session, "PUT", url, payload)
    if error:
        write_operation_log(
            "order_claim_sheet_writeback",
            level="error",
            message="Order claim sheet writeback failed",
            payload={"sheet": sheet_title, "rowNumber": row_number, "error": error},
        )
        return {"ok": False, "message": error, "cell": f"Q{row_number}"}
    return {"ok": True, "cell": f"Q{row_number}", "value": True, "response": data}


def record_sheet_writeback(line_user_id, source_key, sheet_title, row_number, sheet_writeback):
    timestamp = now_iso()
    with get_db() as db:
        db.execute(
            """
            UPDATE order_claim_records
            SET sheet_writeback_status = ?,
                sheet_writeback_message = ?,
                updated_at = ?
            WHERE line_user_id = ?
              AND source_key = ?
            """,
            (
                "ok" if sheet_writeback.get("ok") else "error",
                clean_text(sheet_writeback.get("message") or "", 1000),
                timestamp,
                line_user_id,
                source_key,
            ),
        )
        db.commit()


def safe_criteria(criteria):
    output = dict(criteria)
    if output.get("paymentNo"):
        output["paymentNo"] = mask_value(output["paymentNo"])
    if output.get("lookupValue"):
        output["lookupValue"] = mask_value(output["lookupValue"])
    return output


def mask_value(value):
    text = clean_text(value, 120)
    if len(text) <= 4:
        return "***"
    return f"{text[:2]}***{text[-2:]}"


def list_order_claim_records(filters):
    try:
        limit = min(max(int(filters.get("limit", 100)), 1), 500)
    except (TypeError, ValueError):
        limit = 100

    line_user_id = validate_line_user_id(filters.get("lineUserId"))
    keyword = clean_text(filters.get("q"), 160)
    where = []
    values = []
    if line_user_id:
        where.append("ocr.line_user_id = ?")
        values.append(line_user_id)
    if keyword:
        where.append(
            """
            (
                LOWER(ocr.line_user_id) LIKE ?
                OR LOWER(COALESCE(ocr.line_display_name, '')) LIKE ?
                OR LOWER(COALESCE(ocr.lookup_value, '')) LIKE ?
                OR LOWER(COALESCE(ocr.payment_no, '')) LIKE ?
                OR LOWER(COALESCE(ocr.amount, '')) LIKE ?
            )
            """
        )
        like = f"%{keyword.lower()}%"
        values.extend([like, like, like, like, like])

    where_sql = "WHERE " + " AND ".join(where) if where else ""
    with get_db() as db:
        rows = db.execute(
            f"""
            SELECT
                ocr.id,
                ocr.line_user_id,
                COALESCE(ocr.line_display_name, m.display_name, '') AS line_display_name,
                m.picture_url,
                ocr.claim_type,
                ocr.lookup_value,
                ocr.amount,
                ocr.payment_no,
                ocr.points,
                ocr.source_key,
                ocr.source_sheet,
                ocr.source_row,
                ocr.payment_method,
                ocr.status,
                ocr.sheet_writeback_status,
                ocr.sheet_writeback_message,
                ocr.created_at
            FROM order_claim_records ocr
            LEFT JOIN members m ON m.line_user_id = ocr.line_user_id
            {where_sql}
            ORDER BY ocr.created_at DESC, ocr.id DESC
            LIMIT ?
            """,
            [*values, limit],
        ).fetchall()

    records = [
        {
            "id": row["id"],
            "lineUserId": row["line_user_id"],
            "displayName": row["line_display_name"],
            "pictureUrl": row["picture_url"],
            "claimType": row["claim_type"],
            "lookupValue": row["lookup_value"],
            "amount": row["amount"],
            "paymentNo": row["payment_no"],
            "points": row["points"],
            "sourceKey": row["source_key"],
            "sourceSheet": row["source_sheet"],
            "sourceRow": row["source_row"],
            "paymentMethod": row["payment_method"],
            "status": row["status"],
            "sheetWritebackStatus": row["sheet_writeback_status"],
            "sheetWritebackMessage": row["sheet_writeback_message"],
            "createdAt": row["created_at"],
        }
        for row in rows
    ]
    return {"ok": True, "records": records}, 200
