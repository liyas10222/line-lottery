from functools import wraps

from flask import Blueprint, jsonify, request

from config import Config
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
    get_admin_prizes,
    get_member_spin_limit,
    list_prize_serials,
    reset_member_daily_spin,
    set_global_daily_limit,
    set_member_spin_limit,
    update_prize,
    update_prize_serial,
)


admin_bp = Blueprint("admin", __name__, url_prefix="/api/admin")


def require_admin_token(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not Config.ADMIN_API_TOKEN:
            return jsonify({"ok": False, "message": "尚未設定 ADMIN_API_TOKEN"}), 503

        token = request.headers.get("X-Admin-Token", "")
        if token != Config.ADMIN_API_TOKEN:
            return jsonify({"ok": False, "message": "管理權限不足"}), 401

        return view_func(*args, **kwargs)

    return wrapped


@admin_bp.get("/prizes")
@require_admin_token
def prizes():
    result, status_code = get_admin_prizes()
    return jsonify(result), status_code


@admin_bp.patch("/prizes/<int:prize_id>")
@require_admin_token
def patch_prize(prize_id):
    payload = request.get_json(silent=True) or {}
    result, status_code = update_prize(prize_id, payload)
    return jsonify(result), status_code


@admin_bp.post("/prizes/<int:prize_id>/serials")
@require_admin_token
def create_serials(prize_id):
    payload = request.get_json(silent=True) or {}
    result, status_code = add_prize_serials(prize_id, payload)
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
    return jsonify(result), status_code


@admin_bp.patch("/settings/daily-spin-limit")
@require_admin_token
def patch_daily_spin_limit():
    payload = request.get_json(silent=True) or {}
    result, status_code = set_global_daily_limit(payload)
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
    return jsonify(result), status_code


@admin_bp.post("/members/<line_user_id>/daily-spin/reset")
@require_admin_token
def reset_member_spin(line_user_id):
    payload = request.get_json(silent=True) or {}
    result, status_code = reset_member_daily_spin(line_user_id, payload.get("date"))
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
    return jsonify(result), status_code


@admin_bp.post("/google-sheet/rebuild")
@require_admin_token
def rebuild_google_sheet():
    payload = request.get_json(silent=True) or {}
    if payload.get("confirm") != "REBUILD_LOTTERY_POOL":
        return jsonify({"ok": False, "message": "請確認重建獎池"}), 400

    result, status_code = rebuild_from_google_sheet()
    return jsonify(result), status_code


@admin_bp.post("/google-sheet/reset-records")
@require_admin_token
def reset_google_sheet_records():
    payload = request.get_json(silent=True) or {}
    if payload.get("confirm") != "RESET_SHEET_RECORDS":
        return jsonify({"ok": False, "message": "請確認清空試算表紀錄"}), 400

    result, status_code = reset_google_sheet_lottery_records()
    return jsonify(result), status_code


@admin_bp.post("/google-sheet/reset-records-and-rebuild")
@require_admin_token
def reset_google_sheet_records_and_rebuild():
    payload = request.get_json(silent=True) or {}
    if payload.get("confirm") != "RESET_SHEET_RECORDS_AND_REBUILD":
        return jsonify({"ok": False, "message": "請確認清空試算表紀錄並重建獎池"}), 400

    result, status_code = reset_sheet_records_and_rebuild()
    return jsonify(result), status_code


@admin_bp.post("/google-sheet/setup")
@require_admin_token
def setup_google_sheet():
    result, status_code = ensure_control_sheet()
    return jsonify(result), status_code
