import json
from datetime import datetime
from pathlib import Path

from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account

from config import Config


SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"
DEFAULT_SHEET_NAME = "\u8f49\u76e4"
ASSIGNED_STATUS_LABEL = "\u5df2\u62bd\u4e2d"
ASSIGNED_DISPLAY_NAME_HEADER = "\u62bd\u4e2d\u6703\u54e1\u540d\u7a31"
REDEEM_CONFIRM_HEADER = "\u514c\u63db\u78ba\u8a8d"


def format_sheet_datetime(value):
    if not value:
        return ""
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    return parsed.strftime("%Y/%m/%d %H:%M:%S")


def service_account_path():
    if not Config.GOOGLE_SERVICE_ACCOUNT_FILE:
        return None
    return Path(Config.GOOGLE_SERVICE_ACCOUNT_FILE).expanduser()


def service_account_email():
    if Config.GOOGLE_SERVICE_ACCOUNT_JSON:
        try:
            data = json.loads(Config.GOOGLE_SERVICE_ACCOUNT_JSON)
            return data.get("client_email") or "service account"
        except json.JSONDecodeError:
            return "service account"

    path = service_account_path()
    if not path or not path.exists():
        return "service account"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "service account"
    return data.get("client_email") or "service account"


def get_authorized_session():
    if Config.GOOGLE_SERVICE_ACCOUNT_JSON:
        try:
            service_account_info = json.loads(Config.GOOGLE_SERVICE_ACCOUNT_JSON)
            credentials = service_account.Credentials.from_service_account_info(
                service_account_info,
                scopes=[SHEETS_SCOPE],
            )
            return AuthorizedSession(credentials), None
        except (ValueError, GoogleAuthError) as error:
            return None, f"Failed to read Service Account JSON: {error}"

    path = service_account_path()
    if not path:
        return None, "GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_SERVICE_ACCOUNT_FILE is not configured"
    if not path.exists():
        return None, f"Service Account file not found: {path}"

    try:
        credentials = service_account.Credentials.from_service_account_file(
            path,
            scopes=[SHEETS_SCOPE],
        )
    except (OSError, ValueError, GoogleAuthError) as error:
        return None, f"Failed to read Service Account file: {error}"

    return AuthorizedSession(credentials), None


def request_google_json(session, url):
    try:
        response = session.get(url, timeout=20)
    except Exception as error:
        return None, f"Google Sheets API request failed: {error}"

    if response.status_code == 403:
        return None, f"Google Sheet permission denied. Share the sheet with {service_account_email()}"
    if response.status_code == 404:
        return None, "Google Sheet not found. Check GOOGLE_SHEET_ID"
    if response.status_code >= 400:
        return None, f"Google Sheets API read failed: HTTP {response.status_code} {response.text[:300]}"

    try:
        return response.json(), None
    except ValueError:
        return None, "Google Sheets API returned invalid JSON"


def send_google_json(session, method, url, payload):
    try:
        response = session.request(method, url, json=payload, timeout=20)
    except Exception as error:
        return None, f"Google Sheets API request failed: {error}"

    if response.status_code == 403:
        return None, f"Google Sheet permission denied. Share the sheet with {service_account_email()}"
    if response.status_code >= 400:
        return None, f"Google Sheets API update failed: HTTP {response.status_code} {response.text[:300]}"

    if not response.text:
        return {}, None
    try:
        return response.json(), None
    except ValueError:
        return {}, None


def resolve_sheet_name(session):
    if not Config.GOOGLE_SHEET_ID:
        return Config.GOOGLE_SHEET_NAME or DEFAULT_SHEET_NAME, None

    url = (
        f"https://sheets.googleapis.com/v4/spreadsheets/{Config.GOOGLE_SHEET_ID}"
        "?fields=sheets.properties(sheetId,title)"
    )
    data, error = request_google_json(session, url)
    if error:
        return None, error

    sheets = data.get("sheets", [])
    if Config.GOOGLE_SHEET_GID:
        try:
            target_gid = int(Config.GOOGLE_SHEET_GID)
        except ValueError:
            target_gid = None
        if target_gid is not None:
            for sheet in sheets:
                properties = sheet.get("properties", {})
                if properties.get("sheetId") == target_gid:
                    return properties.get("title"), None

    if Config.GOOGLE_SHEET_NAME:
        for sheet in sheets:
            properties = sheet.get("properties", {})
            if properties.get("title") == Config.GOOGLE_SHEET_NAME:
                return properties.get("title"), None

    if sheets:
        return sheets[0].get("properties", {}).get("title"), None

    return Config.GOOGLE_SHEET_NAME or DEFAULT_SHEET_NAME, None


def mark_serial_assigned_in_sheet(serial, line_user_id, line_display_name, lottery_record_id, assigned_at):
    source_row = serial.get("source_row")
    if not source_row:
        return {"ok": True, "skipped": True, "message": "Serial has no source row"}

    session, error = get_authorized_session()
    if error:
        return {"ok": False, "message": error}

    sheet_name, error = resolve_sheet_name(session)
    if error:
        return {"ok": False, "message": error}

    escaped_sheet_name = sheet_name.replace("'", "''")
    row_number = int(source_row)
    assignment_range = f"'{escaped_sheet_name}'!I{row_number}:L{row_number}"
    display_name_range = f"'{escaped_sheet_name}'!O{row_number}:O{row_number}"
    header_range = f"'{escaped_sheet_name}'!O1"
    redeem_header_range = f"'{escaped_sheet_name}'!P1"
    redeem_confirm_range = f"'{escaped_sheet_name}'!P{row_number}:P{row_number}"
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{Config.GOOGLE_SHEET_ID}/values:batchUpdate"
    payload = {
        "valueInputOption": "RAW",
        "data": [
            {
                "range": header_range,
                "majorDimension": "ROWS",
                "values": [[ASSIGNED_DISPLAY_NAME_HEADER]],
            },
            {
                "range": redeem_header_range,
                "majorDimension": "ROWS",
                "values": [[REDEEM_CONFIRM_HEADER]],
            },
            {
                "range": assignment_range,
                "majorDimension": "ROWS",
                "values": [[
                    ASSIGNED_STATUS_LABEL,
                    line_user_id,
                    str(lottery_record_id),
                    format_sheet_datetime(assigned_at),
                ]],
            },
            {
                "range": display_name_range,
                "majorDimension": "ROWS",
                "values": [[line_display_name or ""]],
            },
            {
                "range": redeem_confirm_range,
                "majorDimension": "ROWS",
                "values": [[False]],
            },
        ],
    }
    result, error = send_google_json(session, "POST", url, payload)
    if error:
        return {"ok": False, "message": error}

    return {
        "ok": True,
        "updatedRows": result.get("totalUpdatedRows"),
        "updatedCells": result.get("totalUpdatedCells"),
    }
