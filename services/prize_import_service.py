import csv
import io

from services.lottery_service import (
    as_bool,
    as_optional_int,
    as_optional_number,
    clean_text,
    get_db,
    now_iso,
    validate_prize_weight_configs,
)
from services.operation_log_service import write_operation_log
from services.google_sheet_service import upsert_prize_rows_to_google_sheet


CSV_HEADERS = [
    "prize_code",
    "prize_name",
    "short_label",
    "weight",
    "stock",
    "requires_serial",
    "is_active",
    "serial_code",
]

CSV_SAMPLE_ROWS = [
    ["COUPON30", "30元折價券", "30元", "18.78", "100", "true", "true", "COUPON30-0001"],
    ["COUPON30", "30元折價券", "30元", "18.78", "100", "true", "true", "COUPON30-0002"],
    ["COUPON170", "170元折價券", "170元", "15.02", "80", "true", "true", "COUPON170-0001"],
    ["THANKS", "銘謝惠顧", "銘謝惠顧", "20", "9999", "false", "true", ""],
]


def csv_template_text():
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CSV_HEADERS)
    writer.writerows(CSV_SAMPLE_ROWS)
    return "\ufeff" + buffer.getvalue()


def normalize_csv_text(value):
    text = value or ""
    if isinstance(text, bytes):
        text = text.decode("utf-8-sig")
    return str(text).lstrip("\ufeff")


def parse_bool_for_import(value, default=True):
    text = clean_text(value, 40).lower()
    if text == "":
        return default
    if text in {"1", "true", "yes", "y", "on", "是", "啟用", "開啟"}:
        return True
    if text in {"0", "false", "no", "n", "off", "否", "停用", "關閉"}:
        return False
    raise ValueError("必須是 true/false")


def parse_csv_rows(csv_text):
    text = normalize_csv_text(csv_text)
    if not text.strip():
        return [], [{"row": 1, "message": "CSV 內容是空的"}]

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return [], [{"row": 1, "message": "缺少表頭"}]

    normalized_headers = [clean_text(header, 80) for header in reader.fieldnames]
    missing_headers = [header for header in CSV_HEADERS if header not in normalized_headers]
    if missing_headers:
        return [], [{"row": 1, "message": f"缺少欄位：{', '.join(missing_headers)}"}]

    parsed_rows = []
    errors = []
    for row_index, raw_row in enumerate(reader, start=2):
        if not any(clean_text(value, 4000) for value in raw_row.values()):
            continue

        row = {key: clean_text(raw_row.get(key), 4000) for key in CSV_HEADERS}
        prize_code = clean_text(row["prize_code"], 80)
        prize_name = clean_text(row["prize_name"], 120)
        if not prize_code:
            errors.append({"row": row_index, "message": "缺少 prize_code"})
        if not prize_name:
            errors.append({"row": row_index, "message": "缺少 prize_name"})

        try:
            weight = as_optional_number(row["weight"], minimum=0)
        except (TypeError, ValueError):
            errors.append({"row": row_index, "message": "weight 必須是 0 以上數字"})
            weight = None

        try:
            stock = as_optional_int(row["stock"], minimum=0)
        except (TypeError, ValueError):
            errors.append({"row": row_index, "message": "stock 必須是 0 以上整數或空白"})
            stock = None

        try:
            requires_serial = parse_bool_for_import(row["requires_serial"], default=bool(row["serial_code"]))
        except ValueError:
            errors.append({"row": row_index, "message": "requires_serial 必須是 true/false"})
            requires_serial = bool(row["serial_code"])

        try:
            is_active = parse_bool_for_import(row["is_active"], default=True)
        except ValueError:
            errors.append({"row": row_index, "message": "is_active 必須是 true/false"})
            is_active = True

        if not requires_serial and row["serial_code"]:
            errors.append({"row": row_index, "message": "requires_serial=false 時 serial_code 必須空白"})

        parsed_rows.append(
            {
                "row": row_index,
                "prizeCode": prize_code,
                "prizeName": prize_name,
                "shortLabel": clean_text(row["short_label"], 24) or prize_name,
                "weight": 0 if weight is None else weight,
                "stock": stock,
                "requiresSerial": requires_serial,
                "isActive": is_active,
                "serialCode": clean_text(row["serial_code"], 120),
            }
        )

    return parsed_rows, errors


def aggregate_rows(rows):
    prizes = {}
    serials = []
    seen_serials_in_file = set()
    errors = []

    for row in rows:
        code = row["prizeCode"]
        if code and code not in prizes:
            prizes[code] = {
                "row": row["row"],
                "code": code,
                "name": row["prizeName"],
                "shortLabel": row["shortLabel"],
                "weight": row["weight"],
                "stock": row["stock"],
                "requiresSerial": row["requiresSerial"],
                "isActive": row["isActive"],
            }
        elif code:
            prize = prizes[code]
            for field, label in [
                ("name", "prize_name"),
                ("shortLabel", "short_label"),
                ("weight", "weight"),
                ("stock", "stock"),
                ("requiresSerial", "requires_serial"),
                ("isActive", "is_active"),
            ]:
                incoming = {
                    "name": row["prizeName"],
                    "shortLabel": row["shortLabel"],
                    "weight": row["weight"],
                    "stock": row["stock"],
                    "requiresSerial": row["requiresSerial"],
                    "isActive": row["isActive"],
                }[field]
                if prize[field] != incoming:
                    errors.append({"row": row["row"], "message": f"同一 prize_code 的 {label} 不一致"})

        serial_code = row["serialCode"]
        if serial_code:
            if serial_code in seen_serials_in_file:
                errors.append({"row": row["row"], "message": f"CSV 內序號重複：{serial_code}"})
            else:
                seen_serials_in_file.add(serial_code)
                serials.append({"row": row["row"], "prizeCode": code, "serialCode": serial_code})

    return prizes, serials, errors


def build_import_summary(rows, errors, prizes, serials):
    with get_db() as db:
        existing_prize_codes = {
            row["code"]
            for row in db.execute("SELECT code FROM prizes").fetchall()
        }
        existing_serial_codes = {
            row["serial_code"]
            for row in db.execute("SELECT serial_code FROM prize_serials").fetchall()
        }

    new_prize_count = sum(1 for code in prizes if code not in existing_prize_codes)
    update_prize_count = sum(1 for code in prizes if code in existing_prize_codes)
    new_serial_count = sum(1 for item in serials if item["serialCode"] not in existing_serial_codes)
    skipped_serial_count = sum(1 for item in serials if item["serialCode"] in existing_serial_codes)

    return {
        "rowCount": len(rows),
        "newPrizeCount": new_prize_count,
        "updatePrizeCount": update_prize_count,
        "newSerialCount": new_serial_count,
        "skippedExistingSerialCount": skipped_serial_count,
        "errorRowCount": len({error["row"] for error in errors}),
        "errors": errors,
    }


def validate_import_weight_total(prizes):
    configs_by_code = {}
    with get_db() as db:
        for row in db.execute("SELECT code, weight, is_active FROM prizes").fetchall():
            configs_by_code[row["code"]] = {
                "code": row["code"],
                "weight": row["weight"],
                "isActive": bool(row["is_active"]),
            }

    for prize in prizes.values():
        configs_by_code[prize["code"]] = {
            "code": prize["code"],
            "weight": prize["weight"],
            "isActive": prize["isActive"],
        }

    return validate_prize_weight_configs(configs_by_code.values())


def preview_prize_import(csv_text):
    rows, parse_errors = parse_csv_rows(csv_text)
    prizes, serials, aggregate_errors = aggregate_rows(rows)
    errors = parse_errors + aggregate_errors
    if not errors:
        weight_total_ok, configured_total_weight = validate_import_weight_total(prizes)
        if not weight_total_ok:
            errors.append({
                "row": 1,
                "message": f"啟用獎項權重總和不可超過 100，目前會變成 {configured_total_weight}",
            })
    summary = build_import_summary(rows, errors, prizes, serials)
    return {"ok": len(errors) == 0, "summary": summary}, 200 if len(errors) == 0 else 400


def import_prize_csv(csv_text):
    rows, parse_errors = parse_csv_rows(csv_text)
    prizes, serials, aggregate_errors = aggregate_rows(rows)
    errors = parse_errors + aggregate_errors
    if not errors:
        weight_total_ok, configured_total_weight = validate_import_weight_total(prizes)
        if not weight_total_ok:
            errors.append({
                "row": 1,
                "message": f"啟用獎項權重總和不可超過 100，目前會變成 {configured_total_weight}",
            })
    if errors:
        summary = build_import_summary(rows, errors, prizes, serials)
        result = {"ok": False, "message": "CSV 驗證失敗", "summary": summary}
        write_operation_log("admin_prize_csv_import", level="error", message="CSV import validation failed", payload=result)
        return result, 400

    timestamp = now_iso()
    stats = {
        "newPrizeCount": 0,
        "updatePrizeCount": 0,
        "newSerialCount": 0,
        "skippedExistingSerialCount": 0,
        "errorRowCount": 0,
        "errors": [],
    }

    with get_db() as db:
        db.begin_immediate()
        try:
            prize_ids = {}
            for prize in prizes.values():
                existing = db.execute(
                    "SELECT id FROM prizes WHERE code = ?",
                    (prize["code"],),
                ).fetchone()
                if existing:
                    db.execute(
                        """
                        UPDATE prizes
                        SET name = ?,
                            short_label = ?,
                            weight = ?,
                            stock = ?,
                            requires_serial = ?,
                            is_active = ?,
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            prize["name"],
                            prize["shortLabel"],
                            prize["weight"],
                            prize["stock"],
                            1 if prize["requiresSerial"] else 0,
                            1 if prize["isActive"] else 0,
                            timestamp,
                            existing["id"],
                        ),
                    )
                    prize_ids[prize["code"]] = existing["id"]
                    stats["updatePrizeCount"] += 1
                else:
                    cursor = db.execute_insert(
                        """
                        INSERT INTO prizes
                            (name, code, short_label, weight, stock, requires_serial, is_active, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            prize["name"],
                            prize["code"],
                            prize["shortLabel"],
                            prize["weight"],
                            prize["stock"],
                            1 if prize["requiresSerial"] else 0,
                            1 if prize["isActive"] else 0,
                            timestamp,
                            timestamp,
                        ),
                    )
                    prize_ids[prize["code"]] = cursor.lastrowid
                    stats["newPrizeCount"] += 1

            for item in serials:
                prize_id = prize_ids.get(item["prizeCode"])
                if not prize_id:
                    stats["errorRowCount"] += 1
                    stats["errors"].append({"row": item["row"], "message": "找不到序號所屬獎項"})
                    continue
                cursor = db.execute(
                    """
                    INSERT OR IGNORE INTO prize_serials
                        (prize_id, serial_code, status, source_sheet, source_row, created_at, updated_at)
                    VALUES (?, ?, 'available', 'csv_import', ?, ?, ?)
                    """,
                    (prize_id, item["serialCode"], item["row"], timestamp, timestamp),
                )
                if cursor.rowcount:
                    stats["newSerialCount"] += 1
                else:
                    stats["skippedExistingSerialCount"] += 1

            db.commit()
        except Exception:
            db.rollback()
            raise

    sheet_rows = [
        {
            "prizeCode": row["prizeCode"],
            "prizeName": row["prizeName"],
            "shortLabel": row["shortLabel"],
            "weight": row["weight"],
            "stock": row["stock"],
            "requiresSerial": row["requiresSerial"],
            "isActive": row["isActive"],
            "serialCode": row["serialCode"],
        }
        for row in rows
    ]
    try:
        sheet_result, sheet_status = upsert_prize_rows_to_google_sheet(sheet_rows)
        if sheet_result.get("ok"):
            row_numbers_by_serial = sheet_result.get("rowNumbersBySerial") or {}
            if row_numbers_by_serial:
                with get_db() as db:
                    for serial_code, source_row in row_numbers_by_serial.items():
                        db.execute(
                            """
                            UPDATE prize_serials
                            SET source_sheet = ?,
                                source_row = ?,
                                updated_at = ?
                            WHERE serial_code = ?
                              AND status = 'available'
                            """,
                            ("google_sheet", source_row, timestamp, serial_code),
                        )
                    db.commit()
        else:
            write_operation_log(
                "google_sheet_prize_upsert",
                level="error",
                message="CSV import committed but Google Sheet upsert failed",
                payload={"statusCode": sheet_status, "result": sheet_result},
            )
    except Exception as error:
        sheet_status = 500
        sheet_result = {"ok": False, "message": str(error)}
        write_operation_log(
            "google_sheet_prize_upsert",
            level="error",
            message="CSV import committed but Google Sheet upsert crashed",
            payload={"error": str(error)},
        )

    stats["sheetWriteback"] = {"statusCode": sheet_status, **sheet_result}
    result = {"ok": True, "importedAt": timestamp, "summary": stats}
    write_operation_log("admin_prize_csv_import", message="CSV import completed", payload=result)
    return result, 200
