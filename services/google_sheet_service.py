import csv
import io
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account

from config import Config
from services.database import reset_sequences
from services.lottery_service import clean_text, get_db, now_iso, now_local
from services.operation_log_service import write_operation_log


SHEETS_READONLY_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"
SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"
SHEET_AVAILABLE_LABEL = "\u53ef\u62bd"
CHINESE_HEADERS = [
    "獎品代碼",
    "獎品名稱",
    "轉盤文字",
    "機率",
    "獎品數量",
    "是否啟用",
    "是否需要序號",
    "序號",
    "序號狀態",
    "抽中會員ID",
    "抽獎紀錄ID",
    "抽中時間",
    "出貨單號",
    "備註",
    "抽中會員名稱",
    "兌換確認",
]


HEADER_ALIASES = {
    "code": ["code", "prizecode", "獎品代碼", "獎項代碼", "獎品編號", "獎項編號"],
    "name": ["name", "prizename", "獎品名稱", "獎項名稱", "品名"],
    "short_label": ["shortlabel", "label", "轉盤文字", "轉盤顯示", "短標籤", "簡稱"],
    "weight": ["weight", "probability", "機率", "中獎機率", "權重", "比重"],
    "stock": ["stock", "quantity", "qty", "數量", "獎品數量", "庫存"],
    "is_active": ["isactive", "active", "啟用", "是否啟用", "上架", "狀態"],
    "requires_serial": ["requiresserial", "需要序號", "是否需要序號", "序號獎品"],
    "serial_code": ["serialcode", "serial", "序號", "獎品序號", "序號清單"],
    "serial_status": ["serialstatus", "序號狀態", "是否已抽中"],
    "assigned_line_user_id": ["assignedlineuserid", "抽中會員id", "抽中會員ID", "lineuserid", "lineUserId"],
    "assigned_display_name": ["assigneddisplayname", "linedisplayname", "displayname", "抽中會員名稱", "抽中會員暱稱", "LINE名稱"],
    "lottery_record_id": ["lotteryrecordid", "抽獎紀錄id", "抽獎紀錄ID"],
    "assigned_at": ["assignedat", "抽中時間", "指派時間"],
    "source_order_no": ["sourceorderno", "orderno", "出貨單號", "訂單編號", "單號"],
    "note": ["note", "備註", "說明"],
    "redeem_confirmed": ["redeemconfirmed", "redeemedconfirmed", "兌換確認", "已兌換確認", "已使用確認", "使用確認"],
}


def normalize_header(value):
    return re.sub(r"[\s_\-:：/／()（）]+", "", clean_text(value, 80).lower())


def normalize_row(row):
    return {normalize_header(key): value for key, value in row.items()}


def get_cell(row, field):
    for alias in HEADER_ALIASES[field]:
        value = row.get(normalize_header(alias))
        if value is not None and str(value).strip() != "":
            return str(value).strip()
    return ""


def parse_bool(value, default=True):
    text = clean_text(value, 40).lower()
    if text == "":
        return default
    if text in {"1", "true", "yes", "y", "on", "是", "啟用", "上架", "開啟", "有效"}:
        return True
    if text in {"0", "false", "no", "n", "off", "否", "停用", "下架", "關閉", "無效"}:
        return False
    return default


def parse_number(value, default=None):
    text = clean_text(value, 40).replace(",", "")
    if text == "":
        return default
    if text.endswith("%"):
        text = text[:-1].strip()
    try:
        number = float(text)
    except ValueError:
        return default
    if number.is_integer():
        return int(number)
    return number


def parse_stock(value):
    text = clean_text(value, 40)
    if text == "" or text in {"不限", "無限", "無庫存限制", "-"}:
        return None
    number = parse_number(text, default=None)
    if number is None:
        return None
    return max(0, int(number))


def parse_serial_status(value):
    text = clean_text(value, 40).lower()
    if text in {"已抽中", "已指派", "assigned", "won"}:
        return "assigned"
    if text in {"已兌換", "已使用", "redeemed", "used"}:
        return "redeemed"
    if text in {"作廢", "void"}:
        return "void"
    return "available"


def split_serial_codes(value):
    text = clean_text(value, 4000)
    if not text:
        return []
    parts = re.split(r"[\n\r,，;；、\t]+", text)
    return [part.strip() for part in parts if part.strip()]


def sheet_csv_url():
    if Config.GOOGLE_SHEET_CSV_URL:
        return Config.GOOGLE_SHEET_CSV_URL
    if Config.GOOGLE_SHEET_ID and Config.GOOGLE_SHEET_GID:
        return (
            f"https://docs.google.com/spreadsheets/d/{Config.GOOGLE_SHEET_ID}"
            f"/export?format=csv&gid={Config.GOOGLE_SHEET_GID}"
        )
    return ""


def fetch_sheet_csv():
    url = sheet_csv_url()
    if not url:
        return None, "尚未設定 GOOGLE_SHEET_ID/GOOGLE_SHEET_GID 或 GOOGLE_SHEET_CSV_URL"

    request = urllib.request.Request(
        url,
        headers={"User-Agent": "line-lottery-sheet-sync/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset), None
    except urllib.error.HTTPError as error:
        if error.code in {401, 403}:
            return None, "Google Sheet 目前無法公開讀取，請發布成 CSV 或開放知道連結可讀"
        return None, f"Google Sheet 讀取失敗：HTTP {error.code}"
    except urllib.error.URLError as error:
        return None, f"Google Sheet 連線失敗：{error.reason}"
    except TimeoutError:
        return None, "Google Sheet 讀取逾時"


def rows_from_csv(csv_text):
    csv_file = io.StringIO(csv_text)
    reader = csv.DictReader(csv_file)
    return list(reader)


def service_account_path():
    if not Config.GOOGLE_SERVICE_ACCOUNT_FILE:
        return None
    return Path(Config.GOOGLE_SERVICE_ACCOUNT_FILE).expanduser()


def service_account_summary():
    if Config.GOOGLE_SERVICE_ACCOUNT_JSON:
        try:
            data = json.loads(Config.GOOGLE_SERVICE_ACCOUNT_JSON)
        except json.JSONDecodeError:
            return {
                "configured": True,
                "exists": False,
                "clientEmail": None,
                "projectId": None,
                "source": "env_json",
                "error": "GOOGLE_SERVICE_ACCOUNT_JSON 不是合法 JSON",
            }

        return {
            "configured": True,
            "exists": True,
            "clientEmail": data.get("client_email"),
            "projectId": data.get("project_id"),
            "source": "env_json",
        }

    path = service_account_path()
    if not path:
        return {"configured": False, "exists": False, "clientEmail": None, "projectId": None, "source": "none"}
    if not path.exists():
        return {"configured": True, "exists": False, "clientEmail": None, "projectId": None, "source": "file"}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"configured": True, "exists": True, "clientEmail": None, "projectId": None, "source": "file"}

    return {
        "configured": True,
        "exists": True,
        "clientEmail": data.get("client_email"),
        "projectId": data.get("project_id"),
        "source": "file",
    }


def get_authorized_session(scopes=None):
    scopes = scopes or [SHEETS_READONLY_SCOPE]

    if Config.GOOGLE_SERVICE_ACCOUNT_JSON:
        try:
            service_account_info = json.loads(Config.GOOGLE_SERVICE_ACCOUNT_JSON)
            credentials = service_account.Credentials.from_service_account_info(
                service_account_info,
                scopes=scopes,
            )
            return AuthorizedSession(credentials), None
        except (ValueError, GoogleAuthError) as error:
            return None, f"Service Account JSON 讀取失敗：{error}"

    path = service_account_path()
    if not path:
        return None, "尚未設定 GOOGLE_SERVICE_ACCOUNT_JSON 或 GOOGLE_SERVICE_ACCOUNT_FILE"
    if not path.exists():
        return None, f"找不到 Service Account 金鑰檔：{path}"

    try:
        credentials = service_account.Credentials.from_service_account_file(
            path,
            scopes=scopes,
        )
    except (OSError, ValueError, GoogleAuthError) as error:
        return None, f"Service Account 金鑰讀取失敗：{error}"

    return AuthorizedSession(credentials), None

def request_google_json(session, url):
    try:
        response = session.get(url, timeout=20)
    except Exception as error:
        return None, f"Google Sheets API 連線失敗：{error}"

    if response.status_code == 403:
        email = service_account_summary().get("clientEmail") or "service account"
        return None, f"Google Sheet 權限不足，請把試算表分享給 {email}"
    if response.status_code == 404:
        return None, "找不到 Google Sheet，請確認 GOOGLE_SHEET_ID"
    if response.status_code >= 400:
        return None, f"Google Sheets API 讀取失敗：HTTP {response.status_code}"

    try:
        return response.json(), None
    except ValueError:
        return None, "Google Sheets API 回傳格式不是 JSON"


def send_google_json(session, method, url, payload):
    try:
        response = session.request(method, url, json=payload, timeout=20)
    except Exception as error:
        return None, f"Google Sheets API 連線失敗：{error}"

    if response.status_code == 403:
        email = service_account_summary().get("clientEmail") or "service account"
        return None, f"Google Sheet 權限不足，請把試算表分享給 {email} 並給予編輯權限"
    if response.status_code == 404:
        return None, "找不到 Google Sheet，請確認 GOOGLE_SHEET_ID"
    if response.status_code >= 400:
        return None, f"Google Sheets API 更新失敗：HTTP {response.status_code} {response.text[:300]}"

    if not response.text:
        return {}, None
    try:
        return response.json(), None
    except ValueError:
        return {}, None


def get_spreadsheet_metadata(session):
    if not Config.GOOGLE_SHEET_ID:
        return None, "尚未設定 GOOGLE_SHEET_ID"

    url = (
        f"https://sheets.googleapis.com/v4/spreadsheets/{Config.GOOGLE_SHEET_ID}"
        "?fields=sheets.properties(sheetId,title,index,gridProperties)"
    )
    return request_google_json(session, url)


def get_sheet_title(session):
    if not Config.GOOGLE_SHEET_ID:
        return None, "尚未設定 GOOGLE_SHEET_ID"

    data, error = get_spreadsheet_metadata(session)
    if error:
        return None, error

    sheets = data.get("sheets", [])
    if not sheets:
        return None, "Google Sheet 沒有可讀取的分頁"

    if Config.GOOGLE_SHEET_NAME:
        for sheet in sheets:
            properties = sheet.get("properties", {})
            if properties.get("title") == Config.GOOGLE_SHEET_NAME:
                return properties.get("title"), None

    if Config.GOOGLE_SHEET_GID:
        try:
            target_gid = int(Config.GOOGLE_SHEET_GID)
        except ValueError:
            return None, "GOOGLE_SHEET_GID 必須是數字"

        for sheet in sheets:
            properties = sheet.get("properties", {})
            if properties.get("sheetId") == target_gid:
                return properties.get("title"), None

        return None, f"找不到 gid={Config.GOOGLE_SHEET_GID} 的分頁"

    return sheets[0].get("properties", {}).get("title"), None


def ensure_control_sheet():
    session, error = get_authorized_session(scopes=[SHEETS_SCOPE])
    if error:
        return {"ok": False, "message": error}, 500

    metadata, error = get_spreadsheet_metadata(session)
    if error:
        return {"ok": False, "message": error}, 502

    sheets = metadata.get("sheets", [])
    target_name = Config.GOOGLE_SHEET_NAME or "轉盤"
    target_gid = int(Config.GOOGLE_SHEET_GID) if Config.GOOGLE_SHEET_GID else None
    target_sheet = None
    sheet_by_gid = None

    for sheet in sheets:
        properties = sheet.get("properties", {})
        if properties.get("title") == target_name:
            target_sheet = properties
        if target_gid is not None and properties.get("sheetId") == target_gid:
            sheet_by_gid = properties

    requests = []
    if target_sheet:
        sheet_id = target_sheet["sheetId"]
    elif sheet_by_gid:
        sheet_id = sheet_by_gid["sheetId"]
        requests.append(
            {
                "updateSheetProperties": {
                    "properties": {"sheetId": sheet_id, "title": target_name},
                    "fields": "title",
                }
            }
        )
    else:
        requests.append(
            {
                "addSheet": {
                    "properties": {
                        "title": target_name,
                        "gridProperties": {"rowCount": 200, "columnCount": len(CHINESE_HEADERS)},
                    }
                }
            }
        )
        sheet_id = None

    if requests:
        url = f"https://sheets.googleapis.com/v4/spreadsheets/{Config.GOOGLE_SHEET_ID}:batchUpdate"
        data, error = send_google_json(session, "POST", url, {"requests": requests})
        if error:
            return {"ok": False, "message": error}, 502

        if sheet_id is None:
            replies = data.get("replies", [])
            for reply in replies:
                added = reply.get("addSheet", {}).get("properties", {})
                if added.get("title") == target_name:
                    sheet_id = added.get("sheetId")

    if sheet_id is None:
        return {"ok": False, "message": "無法取得轉盤分頁 ID"}, 500

    escaped_title = target_name.replace("'", "''")
    range_name = urllib.parse.quote(f"'{escaped_title}'!A1:P1", safe="")
    values_url = (
        f"https://sheets.googleapis.com/v4/spreadsheets/{Config.GOOGLE_SHEET_ID}"
        f"/values/{range_name}?valueInputOption=RAW"
    )
    _, error = send_google_json(
        session,
        "PUT",
        values_url,
        {"range": f"'{target_name}'!A1:P1", "majorDimension": "ROWS", "values": [CHINESE_HEADERS]},
    )
    if error:
        return {"ok": False, "message": error}, 502

    format_requests = [
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": sheet_id,
                    "gridProperties": {"frozenRowCount": 1, "columnCount": len(CHINESE_HEADERS)},
                },
                "fields": "gridProperties.frozenRowCount,gridProperties.columnCount",
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 0,
                    "endRowIndex": 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": len(CHINESE_HEADERS),
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": {"red": 0.12, "green": 0.72, "blue": 0.32},
                        "horizontalAlignment": "CENTER",
                        "textFormat": {
                            "foregroundColor": {"red": 1, "green": 1, "blue": 1},
                            "bold": True,
                        },
                    }
                },
                "fields": "userEnteredFormat(backgroundColor,horizontalAlignment,textFormat)",
            }
        },
        {
            "autoResizeDimensions": {
                "dimensions": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": 0,
                    "endIndex": len(CHINESE_HEADERS),
                }
            }
        },
        {
            "setDataValidation": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 1,
                    "endRowIndex": 10000,
                    "startColumnIndex": 15,
                    "endColumnIndex": 16,
                },
                "rule": {
                    "condition": {"type": "BOOLEAN"},
                    "strict": True,
                    "showCustomUi": True,
                },
            }
        },
    ]
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{Config.GOOGLE_SHEET_ID}:batchUpdate"
    _, error = send_google_json(session, "POST", url, {"requests": format_requests})
    if error:
        return {"ok": False, "message": error}, 502

    return {
        "ok": True,
        "sheetName": target_name,
        "sheetId": sheet_id,
        "headers": CHINESE_HEADERS,
        "serviceAccount": service_account_summary(),
    }, 200


def bool_to_sheet_label(value):
    return "是" if bool(value) else "否"


def prize_sheet_row(row):
    serial_code = clean_text(row.get("serialCode"), 120)
    return [
        clean_text(row.get("prizeCode") or row.get("code"), 80),
        clean_text(row.get("prizeName") or row.get("name"), 120),
        clean_text(row.get("shortLabel") or row.get("short_label"), 24),
        row.get("weight", ""),
        "" if row.get("stock") is None else row.get("stock"),
        bool_to_sheet_label(row.get("isActive", True)),
        bool_to_sheet_label(row.get("requiresSerial", bool(serial_code))),
        serial_code,
        SHEET_AVAILABLE_LABEL if serial_code else "",
        "",
        "",
        "",
        clean_text(row.get("sourceOrderNo"), 80),
        clean_text(row.get("note"), 500),
        "",
        False if serial_code else "",
    ]


def parse_start_row_from_range(range_text):
    match = re.search(r"![A-Z]+(\d+)", range_text or "")
    return int(match.group(1)) if match else None


def upsert_prize_rows_to_google_sheet(rows):
    rows = [row for row in rows if clean_text(row.get("prizeCode") or row.get("code"), 80)]
    if not rows:
        return {"ok": True, "skipped": True, "message": "No rows to write"}, 200

    setup_result, setup_status = ensure_control_sheet()
    if setup_status != 200 or not setup_result.get("ok"):
        return setup_result, setup_status

    session, error = get_authorized_session(scopes=[SHEETS_SCOPE])
    if error:
        return {"ok": False, "message": error}, 500

    sheet_title, error = get_sheet_title(session)
    if error:
        return {"ok": False, "message": error}, 502

    escaped_title = sheet_title.replace("'", "''")
    read_range = f"'{escaped_title}'!A:P"
    range_name = urllib.parse.quote(read_range, safe="")
    url = (
        f"https://sheets.googleapis.com/v4/spreadsheets/{Config.GOOGLE_SHEET_ID}"
        f"/values/{range_name}?majorDimension=ROWS"
    )
    data, error = request_google_json(session, url)
    if error:
        return {"ok": False, "message": error}, 502

    values = data.get("values", [])
    existing_by_serial = {}
    existing_no_serial_by_code = {}
    for row_number, sheet_row in enumerate(values[1:], start=2):
        code = clean_text(sheet_row[0] if len(sheet_row) > 0 else "", 80)
        serial_code = clean_text(sheet_row[7] if len(sheet_row) > 7 else "", 120)
        if serial_code:
            existing_by_serial[serial_code] = row_number
        elif code and code not in existing_no_serial_by_code:
            existing_no_serial_by_code[code] = row_number

    updates = []
    append_values = []
    append_serials = []
    row_numbers_by_serial = {}

    for item in rows:
        sheet_row = prize_sheet_row(item)
        code = sheet_row[0]
        serial_code = sheet_row[7]
        target_row = existing_by_serial.get(serial_code) if serial_code else existing_no_serial_by_code.get(code)

        if target_row:
            updates.append(
                {
                    "range": f"'{escaped_title}'!A{target_row}:G{target_row}",
                    "majorDimension": "ROWS",
                    "values": [sheet_row[:7]],
                }
            )
            updates.append(
                {
                    "range": f"'{escaped_title}'!M{target_row}:N{target_row}",
                    "majorDimension": "ROWS",
                    "values": [[sheet_row[12], sheet_row[13]]],
                }
            )
            if serial_code:
                row_numbers_by_serial[serial_code] = target_row
            continue

        append_values.append(sheet_row)
        append_serials.append(serial_code)

    total_updated_rows = 0
    total_updated_cells = 0
    if updates:
        batch_url = f"https://sheets.googleapis.com/v4/spreadsheets/{Config.GOOGLE_SHEET_ID}/values:batchUpdate"
        for start in range(0, len(updates), 500):
            payload = {"valueInputOption": "RAW", "data": updates[start : start + 500]}
            result, error = send_google_json(session, "POST", batch_url, payload)
            if error:
                return {"ok": False, "message": error}, 502
            total_updated_rows += result.get("totalUpdatedRows", 0)
            total_updated_cells += result.get("totalUpdatedCells", 0)

    appended_rows = 0
    appended_cells = 0
    if append_values:
        append_range = urllib.parse.quote(f"'{escaped_title}'!A:P", safe="")
        append_url = (
            f"https://sheets.googleapis.com/v4/spreadsheets/{Config.GOOGLE_SHEET_ID}"
            f"/values/{append_range}:append?valueInputOption=RAW&insertDataOption=INSERT_ROWS"
        )
        result, error = send_google_json(
            session,
            "POST",
            append_url,
            {"majorDimension": "ROWS", "values": append_values},
        )
        if error:
            return {"ok": False, "message": error}, 502

        updates_result = result.get("updates", {})
        appended_rows = updates_result.get("updatedRows", 0)
        appended_cells = updates_result.get("updatedCells", 0)
        start_row = parse_start_row_from_range(updates_result.get("updatedRange"))
        if start_row:
            for offset, serial_code in enumerate(append_serials):
                if serial_code:
                    row_numbers_by_serial[serial_code] = start_row + offset

    result = {
        "ok": True,
        "sheetName": sheet_title,
        "updatedRows": total_updated_rows,
        "updatedCells": total_updated_cells,
        "appendedRows": appended_rows,
        "appendedCells": appended_cells,
        "rowNumbersBySerial": row_numbers_by_serial,
    }
    write_operation_log("google_sheet_prize_upsert", message="Prize rows upserted to Google Sheet", payload=result)
    return result, 200


def rows_from_values(values):
    if not values:
        return []
    headers = [clean_text(header, 120) for header in values[0]]
    rows = []
    for raw_row in values[1:]:
        row = {}
        for index, header in enumerate(headers):
            if not header:
                continue
            row[header] = raw_row[index] if index < len(raw_row) else ""
        rows.append(row)
    return rows


def fetch_rows_from_service_account():
    session, error = get_authorized_session()
    if error:
        return None, error

    sheet_title, error = get_sheet_title(session)
    if error:
        return None, error
    if not sheet_title:
        return None, "找不到可讀取的 Google Sheet 分頁名稱"

    escaped_title = sheet_title.replace("'", "''")
    range_name = urllib.parse.quote(f"'{escaped_title}'!A:Z", safe="")
    url = (
        f"https://sheets.googleapis.com/v4/spreadsheets/{Config.GOOGLE_SHEET_ID}"
        f"/values/{range_name}?majorDimension=ROWS"
    )
    data, error = request_google_json(session, url)
    if error:
        return None, error

    values = data.get("values", [])
    return rows_from_values(values), None


def parse_bool(value, default=True):
    text = clean_text(value, 40).lower()
    if text == "":
        return default
    if text in {"1", "true", "yes", "y", "on", "\u662f", "\u555f\u7528", "\u958b\u555f"}:
        return True
    if text in {"0", "false", "no", "n", "off", "\u5426", "\u505c\u7528", "\u95dc\u9589"}:
        return False
    return default


def parse_serial_status(value):
    text = clean_text(value, 40).lower()
    if text in {"\u5df2\u62bd\u4e2d", "\u5df2\u4e2d", "assigned", "won"}:
        return "assigned"
    if text in {"\u5df2\u514c\u63db", "\u5df2\u4f7f\u7528", "redeemed", "used"}:
        return "redeemed"
    if text in {"\u4f5c\u5ee2", "void"}:
        return "void"
    return "available"


def sync_prize_rows(rows, source_sheet="google_sheet", rebuild=False):
    timestamp = now_iso()
    stats = {
        "mode": "rebuild" if rebuild else "sync",
        "prizesDeleted": 0,
        "serialsDeleted": 0,
        "recordsDeleted": 0,
        "dailySpinDeleted": 0,
        "prizesCreated": 0,
        "prizesUpdated": 0,
        "prizesDisabled": 0,
        "serialsCreated": 0,
        "serialsUpdated": 0,
        "serialsVoided": 0,
        "serialsSkipped": 0,
        "rowsSkipped": 0,
        "errors": [],
    }
    seen_codes = set()
    seen_serial_codes = set()

    with get_db() as db:
        if rebuild:
            stats["recordsDeleted"] = db.execute("SELECT COUNT(*) AS count FROM lottery_records").fetchone()["count"]
            stats["dailySpinDeleted"] = db.execute("SELECT COUNT(*) AS count FROM daily_spin").fetchone()["count"]
            stats["serialsDeleted"] = db.execute("SELECT COUNT(*) AS count FROM prize_serials").fetchone()["count"]
            stats["prizesDeleted"] = db.execute("SELECT COUNT(*) AS count FROM prizes").fetchone()["count"]
            db.execute("UPDATE lottery_records SET prize_id = NULL, prize_serial_id = NULL")
            db.execute("UPDATE prize_serials SET lottery_record_id = NULL")
            db.execute("DELETE FROM lottery_records")
            db.execute("DELETE FROM daily_spin")
            db.execute("DELETE FROM prize_serials")
            db.execute("DELETE FROM prizes")
            reset_sequences(db, ["prizes", "prize_serials", "lottery_records", "daily_spin"])

        for index, raw_row in enumerate(rows, start=2):
            row = normalize_row(raw_row)
            if not any(str(value).strip() for value in row.values()):
                stats["rowsSkipped"] += 1
                continue

            code = clean_text(get_cell(row, "code"), 80)
            if not code:
                stats["rowsSkipped"] += 1
                stats["errors"].append({"row": index, "message": "缺少獎品代碼"})
                continue
            seen_codes.add(code)

            serial_codes = split_serial_codes(get_cell(row, "serial_code"))
            note = clean_text(get_cell(row, "note"), 500)
            source_order_no = clean_text(get_cell(row, "source_order_no"), 80)

            existing = db.execute(
                """
                SELECT id, name, short_label, weight, stock, requires_serial, is_active
                FROM prizes
                WHERE code = ?
                """,
                (code,),
            ).fetchone()

            raw_name = clean_text(get_cell(row, "name"), 120)
            raw_short_label = clean_text(get_cell(row, "short_label"), 24)
            raw_weight = get_cell(row, "weight")
            raw_stock = get_cell(row, "stock")
            raw_requires_serial = get_cell(row, "requires_serial")
            raw_is_active = get_cell(row, "is_active")

            if existing:
                prize_id = existing["id"]
                name = raw_name or existing["name"]
                short_label = raw_short_label or existing["short_label"] or name
                weight = parse_number(raw_weight, default=existing["weight"])
                stock = parse_stock(raw_stock) if raw_stock != "" else existing["stock"]
                requires_serial = parse_bool(
                    raw_requires_serial,
                    default=bool(existing["requires_serial"]) or bool(serial_codes),
                )
                is_active = parse_bool(raw_is_active, default=bool(existing["is_active"]))
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
                        name,
                        short_label,
                        weight,
                        stock,
                        1 if requires_serial else 0,
                        1 if is_active else 0,
                        timestamp,
                        prize_id,
                    ),
                )
                stats["prizesUpdated"] += 1
            else:
                name = raw_name or code
                short_label = raw_short_label or name
                weight = parse_number(raw_weight, default=0)
                stock = parse_stock(raw_stock)
                requires_serial = parse_bool(raw_requires_serial, default=bool(serial_codes))
                is_active = parse_bool(raw_is_active, default=True)
                cursor = db.execute_insert(
                    """
                    INSERT INTO prizes
                        (name, code, short_label, weight, stock, requires_serial, is_active, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        name,
                        code,
                        short_label,
                        weight,
                        stock,
                        1 if requires_serial else 0,
                        1 if is_active else 0,
                        timestamp,
                        timestamp,
                    ),
                )
                prize_id = cursor.lastrowid
                stats["prizesCreated"] += 1

            for serial_code in serial_codes:
                serial_code = clean_text(serial_code, 120)
                if not serial_code:
                    stats["serialsSkipped"] += 1
                    continue
                seen_serial_codes.add(serial_code)

                redeem_confirmed = parse_bool(get_cell(row, "redeem_confirmed"), default=False)
                sheet_status = "redeemed" if redeem_confirmed else parse_serial_status(get_cell(row, "serial_status"))
                sheet_assigned_line_user_id = None
                sheet_assigned_display_name = None
                sheet_lottery_record_id = None
                sheet_assigned_at = None
                sheet_checked_at = timestamp if sheet_status == "redeemed" else None
                sheet_checked_by = "google_sheet" if sheet_status == "redeemed" else None
                cursor = db.execute(
                    """
                    INSERT OR IGNORE INTO prize_serials
                        (
                            prize_id,
                            serial_code,
                            status,
                            assigned_line_user_id,
                            assigned_display_name,
                            lottery_record_id,
                            assigned_at,
                            checked_at,
                            checked_by,
                            source_order_no,
                            source_sheet,
                            source_row,
                            note,
                            created_at,
                            updated_at
                        )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        prize_id,
                        serial_code,
                        sheet_status,
                        sheet_assigned_line_user_id,
                        sheet_assigned_display_name,
                        sheet_lottery_record_id,
                        sheet_assigned_at,
                        sheet_checked_at,
                        sheet_checked_by,
                        source_order_no,
                        source_sheet,
                        index,
                        note,
                        timestamp,
                        timestamp,
                    ),
                )
                if cursor.rowcount:
                    stats["serialsCreated"] += 1
                else:
                    existing_serial = db.execute(
                        """
                        SELECT
                            id,
                            prize_id,
                            status,
                            assigned_line_user_id,
                            assigned_display_name,
                            lottery_record_id,
                            assigned_at,
                            checked_at,
                            checked_by
                        FROM prize_serials
                        WHERE serial_code = ?
                        """,
                        (serial_code,),
                    ).fetchone()
                    if not existing_serial:
                        stats["serialsSkipped"] += 1
                        continue

                    has_local_assignment = (
                        existing_serial["assigned_line_user_id"] is not None
                        or existing_serial["lottery_record_id"] is not None
                    )
                    keep_local_assignment = has_local_assignment and sheet_status == "available"
                    next_status = existing_serial["status"] if keep_local_assignment else sheet_status
                    next_prize_id = existing_serial["prize_id"] if has_local_assignment else prize_id
                    next_assigned_line_user_id = (
                        existing_serial["assigned_line_user_id"]
                        if keep_local_assignment
                        else sheet_assigned_line_user_id or existing_serial["assigned_line_user_id"]
                    )
                    next_assigned_display_name = (
                        existing_serial["assigned_display_name"]
                        if keep_local_assignment
                        else sheet_assigned_display_name or existing_serial["assigned_display_name"]
                    )
                    next_lottery_record_id = (
                        existing_serial["lottery_record_id"]
                        if keep_local_assignment
                        else sheet_lottery_record_id or existing_serial["lottery_record_id"]
                    )
                    next_assigned_at = (
                        existing_serial["assigned_at"]
                        if keep_local_assignment
                        else sheet_assigned_at or existing_serial["assigned_at"]
                    )
                    next_checked_at = existing_serial["checked_at"]
                    next_checked_by = existing_serial["checked_by"]
                    if next_status == "redeemed":
                        next_checked_at = existing_serial["checked_at"] or timestamp
                        next_checked_by = existing_serial["checked_by"] or "google_sheet"

                    if next_status == "available" and not has_local_assignment:
                        next_assigned_line_user_id = None
                        next_assigned_display_name = None
                        next_lottery_record_id = None
                        next_assigned_at = None
                        next_checked_at = None
                        next_checked_by = None

                    db.execute(
                        """
                        UPDATE prize_serials
                        SET prize_id = ?,
                            status = ?,
                            assigned_line_user_id = ?,
                            assigned_display_name = ?,
                            lottery_record_id = ?,
                            assigned_at = ?,
                            checked_at = ?,
                            checked_by = ?,
                            source_order_no = ?,
                            source_sheet = ?,
                            source_row = ?,
                            note = ?,
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            next_prize_id,
                            next_status,
                            next_assigned_line_user_id,
                            next_assigned_display_name,
                            next_lottery_record_id,
                            next_assigned_at,
                            next_checked_at,
                            next_checked_by,
                            source_order_no,
                            source_sheet,
                            index,
                            note,
                            timestamp,
                            existing_serial["id"],
                        ),
                    )
                    stats["serialsUpdated"] += 1

        if seen_serial_codes:
            placeholders = ",".join("?" for _ in seen_serial_codes)
            cursor = db.execute(
                f"""
                UPDATE prize_serials
                SET status = 'void',
                    updated_at = ?
                WHERE serial_code NOT IN ({placeholders})
                  AND status = 'available'
                """,
                [timestamp, *seen_serial_codes],
            )
            stats["serialsVoided"] = cursor.rowcount
        elif seen_codes:
            cursor = db.execute(
                """
                UPDATE prize_serials
                SET status = 'void',
                    updated_at = ?
                WHERE status = 'available'
                """,
                (timestamp,),
            )
            stats["serialsVoided"] = cursor.rowcount

        if seen_codes:
            placeholders = ",".join("?" for _ in seen_codes)
            cursor = db.execute(
                f"""
                UPDATE prizes
                SET is_active = 0,
                    updated_at = ?
                WHERE code NOT IN ({placeholders})
                  AND is_active = 1
                """,
                [timestamp, *seen_codes],
            )
            stats["prizesDisabled"] = cursor.rowcount

        db.execute(
            """
            INSERT INTO app_settings (key, value, created_at, updated_at)
            VALUES ('google_sheet_last_sync_at', ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (timestamp, timestamp, timestamp),
        )
        db.commit()

    return stats


def sync_from_google_sheet():
    summary = service_account_summary()
    if summary["configured"] and summary["exists"]:
        rows, error = fetch_rows_from_service_account()
        if error:
            write_operation_log(
                "google_sheet_sync",
                level="error",
                message="Google Sheet sync failed",
                payload={"source": "service_account", "error": error},
            )
            return {"ok": False, "message": error}, 502

        stats = sync_prize_rows(rows, source_sheet=f"google_sheet:{Config.GOOGLE_SHEET_NAME}")
        result = {"ok": True, "syncedAt": now_iso(), "source": "service_account", "stats": stats}
        write_operation_log("google_sheet_sync", message="Google Sheet sync completed", payload=result)
        return result, 200

    csv_text, error = fetch_sheet_csv()
    if error:
        write_operation_log(
            "google_sheet_sync",
            level="error",
            message="Google Sheet sync failed",
            payload={"source": "csv", "error": error},
        )
        return {"ok": False, "message": error}, 502

    rows = rows_from_csv(csv_text)
    stats = sync_prize_rows(rows, source_sheet=f"google_sheet:{Config.GOOGLE_SHEET_GID}")
    result = {"ok": True, "syncedAt": now_iso(), "source": "csv", "stats": stats}
    write_operation_log("google_sheet_sync", message="Google Sheet sync completed", payload=result)
    return result, 200


def reset_google_sheet_lottery_records():
    session, error = get_authorized_session(scopes=[SHEETS_SCOPE])
    if error:
        write_operation_log("google_sheet_reset_records", level="error", message="Google Sheet reset failed", payload={"error": error})
        return {"ok": False, "message": error}, 500

    sheet_title, error = get_sheet_title(session)
    if error:
        write_operation_log("google_sheet_reset_records", level="error", message="Google Sheet reset failed", payload={"error": error})
        return {"ok": False, "message": error}, 502

    escaped_title = sheet_title.replace("'", "''")
    read_range = f"'{escaped_title}'!A:P"
    range_name = urllib.parse.quote(read_range, safe="")
    url = (
        f"https://sheets.googleapis.com/v4/spreadsheets/{Config.GOOGLE_SHEET_ID}"
        f"/values/{range_name}?majorDimension=ROWS"
    )
    data, error = request_google_json(session, url)
    if error:
        write_operation_log("google_sheet_reset_records", level="error", message="Google Sheet reset failed", payload={"error": error})
        return {"ok": False, "message": error}, 502

    updates = []
    reset_row_count = 0
    rows = data.get("values", [])
    for index, row in enumerate(rows[1:], start=2):
        serial_code = row[7].strip() if len(row) > 7 else ""
        if not serial_code:
            continue
        reset_row_count += 1
        updates.append(
            {
                "range": f"'{escaped_title}'!I{index}:L{index}",
                "majorDimension": "ROWS",
                "values": [[SHEET_AVAILABLE_LABEL, "", "", ""]],
            }
        )
        updates.append(
            {
                "range": f"'{escaped_title}'!O{index}:O{index}",
                "majorDimension": "ROWS",
                "values": [[""]],
            }
        )
        updates.append(
            {
                "range": f"'{escaped_title}'!P{index}:P{index}",
                "majorDimension": "ROWS",
                "values": [[False]],
            }
        )

    if not updates:
        return {
            "ok": True,
            "sheetName": sheet_title,
            "resetRows": 0,
            "message": "No serial rows found",
        }, 200

    total_updated_rows = 0
    total_updated_cells = 0
    batch_url = f"https://sheets.googleapis.com/v4/spreadsheets/{Config.GOOGLE_SHEET_ID}/values:batchUpdate"
    for start in range(0, len(updates), 500):
        payload = {"valueInputOption": "RAW", "data": updates[start : start + 500]}
        result, error = send_google_json(session, "POST", batch_url, payload)
        if error:
            write_operation_log("google_sheet_reset_records", level="error", message="Google Sheet reset failed", payload={"error": error})
            return {"ok": False, "message": error}, 502
        total_updated_rows += result.get("totalUpdatedRows", 0)
        total_updated_cells += result.get("totalUpdatedCells", 0)

    result = {
        "ok": True,
        "sheetName": sheet_title,
        "resetRows": reset_row_count,
        "updatedRows": total_updated_rows,
        "updatedCells": total_updated_cells,
    }
    write_operation_log("google_sheet_reset_records", message="Google Sheet records reset", payload=result)
    return result, 200


def reset_sheet_records_and_rebuild():
    reset_result, reset_status = reset_google_sheet_lottery_records()
    if reset_status != 200 or not reset_result.get("ok"):
        return reset_result, reset_status

    rebuild_result, rebuild_status = rebuild_from_google_sheet()
    if isinstance(rebuild_result, dict):
        rebuild_result["sheetReset"] = reset_result
    return rebuild_result, rebuild_status


def rebuild_from_google_sheet():
    summary = service_account_summary()
    if summary["configured"] and summary["exists"]:
        rows, error = fetch_rows_from_service_account()
        if error:
            write_operation_log("lottery_pool_rebuild", level="error", message="Lottery pool rebuild failed", payload={"source": "service_account", "error": error})
            return {"ok": False, "message": error}, 502

        stats = sync_prize_rows(
            rows,
            source_sheet=f"google_sheet:{Config.GOOGLE_SHEET_GID or Config.GOOGLE_SHEET_NAME}",
            rebuild=True,
        )
        result = {"ok": True, "rebuiltAt": now_iso(), "source": "service_account", "stats": stats}
        write_operation_log("lottery_pool_rebuild", message="Lottery pool rebuilt", payload=result)
        return result, 200

    csv_text, error = fetch_sheet_csv()
    if error:
        write_operation_log("lottery_pool_rebuild", level="error", message="Lottery pool rebuild failed", payload={"source": "csv", "error": error})
        return {"ok": False, "message": error}, 502

    rows = rows_from_csv(csv_text)
    stats = sync_prize_rows(rows, source_sheet=f"google_sheet:{Config.GOOGLE_SHEET_GID}", rebuild=True)
    result = {"ok": True, "rebuiltAt": now_iso(), "source": "csv", "stats": stats}
    write_operation_log("lottery_pool_rebuild", message="Lottery pool rebuilt", payload=result)
    return result, 200


def google_sheet_status():
    with get_db() as db:
        row = db.execute(
            "SELECT value FROM app_settings WHERE key = 'google_sheet_last_sync_at'"
        ).fetchone()

    return {
        "ok": True,
        "enabled": Config.SHEET_SYNC_ENABLED,
        "intervalSeconds": Config.SHEET_SYNC_INTERVAL_SECONDS,
        "sheetId": Config.GOOGLE_SHEET_ID,
        "gid": Config.GOOGLE_SHEET_GID,
        "sheetName": Config.GOOGLE_SHEET_NAME,
        "csvUrlConfigured": bool(Config.GOOGLE_SHEET_CSV_URL),
        "serviceAccount": service_account_summary(),
        "lastSyncAt": row["value"] if row else None,
    }, 200


def maybe_auto_sync_from_google_sheet():
    if not Config.SHEET_SYNC_ENABLED:
        return None

    try:
        with get_db() as db:
            row = db.execute(
                "SELECT value FROM app_settings WHERE key = 'google_sheet_last_sync_at'"
            ).fetchone()

        if row:
            try:
                last_sync = datetime.fromisoformat(row["value"])
                if now_local() - last_sync < timedelta(seconds=Config.SHEET_SYNC_INTERVAL_SECONDS):
                    return None
            except ValueError:
                pass

        result, status_code = sync_from_google_sheet()
        return {"statusCode": status_code, "result": result}
    except Exception as error:
        write_operation_log(
            "google_sheet_auto_sync",
            level="error",
            message="Google Sheet auto sync failed",
            payload={"error": str(error)},
        )
        return {"statusCode": 500, "result": {"ok": False, "message": str(error)}}
