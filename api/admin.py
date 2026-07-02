from functools import wraps

from flask import Blueprint, jsonify, request

from config import Config
from services.backup_service import export_backup, import_backup
from services.google_sheet_service import (
    ensure_control_sheet,
    google_sheet_status,
    rebuild_from_google_sheet,
    reset_google_sheet_lottery_records,
    reset_sheet_records_and_rebuild,
    sync_from_google_sheet,
)
from services.lottery_service import (
    add_prize_serials,
    add_admin_line_user,
    delete_admin_line_user,
    get_admin_prizes,
    get_member_spin_limit,
    is_admin_line_user_id,
    list_admin_line_users,
    list_members,
    list_prize_serials,
    reset_member_daily_spin,
    set_global_daily_limit,
    set_member_spin_limit,
    update_prize,
    update_prize_serial,
)
from services.operation_log_service import list_operation_logs, write_operation_log


admin_bp = Blueprint("admin", __name__, url_prefix="/api/admin")


def require_admin_token(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not Config.ADMIN_API_TOKEN:
            return jsonify({"ok": False, "message": "尚未設定 ADMIN_API_TOKEN"}), 503

        token = request.headers.get("X-Admin-Token", "")
        if token != Config.ADMIN_API_TOKEN:
            return jsonify({"ok": False, "message": "管理權限不足"}), 401

        admin_line_user_id = request_admin_line_user_id({})
        if admin_line_user_id and not is_admin_line_user_id(admin_line_user_id):
            return jsonify({"ok": False, "message": "LINE 管理員權限不足"}), 403

        return view_func(*args, **kwargs)

    return wrapped


def request_admin_line_user_id(payload=None):
    payload = payload or {}
    return (
        request.headers.get("X-Admin-Line-User-Id", "").strip()
        or payload.get("adminLineUserId")
        or request.args.get("adminLineUserId", "").strip()
        or None
    )


def log_admin_action(event_type, result, status_code, payload=None, message="Admin action"):
    write_operation_log(
        event_type,
        level="info" if 200 <= status_code < 400 and result.get("ok") else "error",
        admin_line_user_id=request_admin_line_user_id(payload),
        message=message,
        payload={"statusCode": status_code, "request": payload or {}, "result": result},
    )


@admin_bp.get("/prizes")
@require_admin_token
def prizes():
    result, status_code = get_admin_prizes()
    return jsonify(result), status_code


@admin_bp.get("/members")
@require_admin_token
def members():
    result, status_code = list_members(request.args)
    return jsonify(result), status_code


@admin_bp.get("/admin-users")
@require_admin_token
def admin_users():
    result, status_code = list_admin_line_users()
    return jsonify(result), status_code


@admin_bp.post("/admin-users")
@require_admin_token
def create_admin_user():
    payload = request.get_json(silent=True) or {}
    result, status_code = add_admin_line_user(payload)
    log_admin_action("admin_line_user_create", result, status_code, payload, "Admin LINE user added")
    return jsonify(result), status_code


@admin_bp.delete("/admin-users/<line_user_id>")
@require_admin_token
def remove_admin_user(line_user_id):
    result, status_code = delete_admin_line_user(line_user_id)
    log_admin_action("admin_line_user_delete", result, status_code, {"lineUserId": line_user_id}, "Admin LINE user deleted")
    return jsonify(result), status_code


@admin_bp.patch("/prizes/<int:prize_id>")
@require_admin_token
def patch_prize(prize_id):
    payload = request.get_json(silent=True) or {}
    result, status_code = update_prize(prize_id, payload)
    log_admin_action("admin_prize_update", result, status_code, payload, "Prize updated")
    return jsonify(result), status_code


@admin_bp.post("/prizes/<int:prize_id>/serials")
@require_admin_token
def create_serials(prize_id):
    payload = request.get_json(silent=True) or {}
    result, status_code = add_prize_serials(prize_id, payload)
    log_admin_action("admin_prize_serials_create", result, status_code, payload, "Prize serials created")
    return jsonify(result), status_code


@admin_bp.get("/serials")
@require_admin_token
def serials():
    result, status_code = list_prize_serials(request.args)
    return jsonify(result), status_code


@admin_bp.patch("/serials/<int:serial_id>")
@require_admin_token
def patch_serial(serial_id):
    payload = request.get_json(silent=True) or {}
    result, status_code = update_prize_serial(serial_id, payload)
    log_admin_action("admin_prize_serial_update", result, status_code, payload, "Prize serial updated")
    return jsonify(result), status_code


@admin_bp.patch("/settings/daily-spin-limit")
@require_admin_token
def patch_daily_spin_limit():
    payload = request.get_json(silent=True) or {}
    result, status_code = set_global_daily_limit(payload)
    log_admin_action("admin_global_daily_limit_update", result, status_code, payload, "Global daily limit updated")
    return jsonify(result), status_code


@admin_bp.get("/members/<line_user_id>/spin-limit")
@require_admin_token
def member_spin_limit(line_user_id):
    result, status_code = get_member_spin_limit(line_user_id)
    return jsonify(result), status_code


@admin_bp.put("/members/<line_user_id>/spin-limit")
@admin_bp.patch("/members/<line_user_id>/spin-limit")
@require_admin_token
def patch_member_spin_limit(line_user_id):
    payload = request.get_json(silent=True) or {}
    result, status_code = set_member_spin_limit(line_user_id, payload)
    write_operation_log(
        "admin_member_spin_limit_update",
        level="info" if 200 <= status_code < 400 and result.get("ok") else "error",
        line_user_id=line_user_id,
        admin_line_user_id=request_admin_line_user_id(payload),
        message="Member spin limit updated",
        payload={"statusCode": status_code, "request": payload, "result": result},
    )
    return jsonify(result), status_code


@admin_bp.post("/members/<line_user_id>/daily-spin/reset")
@require_admin_token
def reset_member_spin(line_user_id):
    payload = request.get_json(silent=True) or {}
    result, status_code = reset_member_daily_spin(line_user_id, payload.get("date"))
    write_operation_log(
        "admin_member_daily_spin_reset",
        level="info" if 200 <= status_code < 400 and result.get("ok") else "error",
        line_user_id=line_user_id,
        admin_line_user_id=request_admin_line_user_id(payload),
        message="Member daily spin reset",
        payload={"statusCode": status_code, "request": payload, "result": result},
    )
    return jsonify(result), status_code


@admin_bp.get("/google-sheet")
@require_admin_token
def get_google_sheet_status():
    result, status_code = google_sheet_status()
    return jsonify(result), status_code


@admin_bp.post("/google-sheet/sync")
@require_admin_token
def sync_google_sheet():
    result, status_code = sync_from_google_sheet()
    log_admin_action("admin_google_sheet_sync", result, status_code, {}, "Google Sheet sync requested")
    return jsonify(result), status_code


@admin_bp.post("/google-sheet/rebuild")
@require_admin_token
def rebuild_google_sheet():
    payload = request.get_json(silent=True) or {}
    if payload.get("confirm") != "REBUILD_LOTTERY_POOL":
        return jsonify({"ok": False, "message": "請確認重建獎池"}), 400

    result, status_code = rebuild_from_google_sheet()
    log_admin_action("admin_lottery_pool_rebuild", result, status_code, payload, "Lottery pool rebuild requested")
    return jsonify(result), status_code


@admin_bp.post("/google-sheet/reset-records")
@require_admin_token
def reset_google_sheet_records():
    payload = request.get_json(silent=True) or {}
    if payload.get("confirm") != "RESET_SHEET_RECORDS":
        return jsonify({"ok": False, "message": "請確認清空試算表紀錄"}), 400

    result, status_code = reset_google_sheet_lottery_records()
    log_admin_action("admin_google_sheet_reset_records", result, status_code, payload, "Google Sheet records reset requested")
    return jsonify(result), status_code


@admin_bp.post("/google-sheet/reset-records-and-rebuild")
@require_admin_token
def reset_google_sheet_records_and_rebuild():
    payload = request.get_json(silent=True) or {}
    if payload.get("confirm") != "RESET_SHEET_RECORDS_AND_REBUILD":
        return jsonify({"ok": False, "message": "請確認清空試算表紀錄並重建獎池"}), 400

    result, status_code = reset_sheet_records_and_rebuild()
    log_admin_action("admin_google_sheet_reset_and_rebuild", result, status_code, payload, "Google Sheet records reset and rebuild requested")
    return jsonify(result), status_code


@admin_bp.post("/google-sheet/setup")
@require_admin_token
def setup_google_sheet():
    result, status_code = ensure_control_sheet()
    log_admin_action("admin_google_sheet_setup", result, status_code, {}, "Google Sheet setup requested")
    return jsonify(result), status_code


@admin_bp.get("/operation-logs")
@require_admin_token
def operation_logs():
    result, status_code = list_operation_logs(
        limit=request.args.get("limit", 100),
        event_type=request.args.get("eventType"),
        level=request.args.get("level"),
    )
    return jsonify(result), status_code


@admin_bp.get("/google-sheet/writeback-failures")
@require_admin_token
def google_sheet_writeback_failures():
    result, status_code = list_operation_logs(
        limit=request.args.get("limit", 50),
        event_type="google_sheet_writeback",
        level="error",
    )
    return jsonify(result), status_code


@admin_bp.post("/backup/export")
@require_admin_token
def backup_export():
    result, status_code = export_backup()
    table_counts = {
        table_name: len(rows)
        for table_name, rows in result.get("tables", {}).items()
        if isinstance(rows, list)
    }
    write_operation_log(
        "admin_backup_export",
        level="info" if 200 <= status_code < 400 and result.get("ok") else "error",
        admin_line_user_id=request_admin_line_user_id({}),
        message="Backup export requested",
        payload={
            "statusCode": status_code,
            "ok": result.get("ok"),
            "exportedAt": result.get("exportedAt"),
            "databaseMode": result.get("databaseMode"),
            "tableCounts": table_counts,
        },
    )
    return jsonify(result), status_code


@admin_bp.post("/backup/import")
@require_admin_token
def backup_import():
    payload = request.get_json(silent=True) or {}
    result, status_code = import_backup(payload)
    log_admin_action("admin_backup_import", result, status_code, {}, "Backup import requested")
    return jsonify(result), status_code
