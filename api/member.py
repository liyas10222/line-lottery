from flask import Blueprint, jsonify, request

from services.lottery_service import is_admin_line_user_id, sync_member


member_bp = Blueprint("member", __name__, url_prefix="/api/member")


@member_bp.post("")
def sync():
    payload = request.get_json(silent=True) or {}
    result, status_code = sync_member(payload)
    return jsonify(result), status_code


@member_bp.post("/sync")
def sync_alias():
    payload = request.get_json(silent=True) or {}
    result, status_code = sync_member(payload)
    return jsonify(result), status_code


@member_bp.get("/admin-status")
def admin_status():
    line_user_id = request.args.get("lineUserId", "").strip()
    return jsonify({"ok": True, "isAdmin": is_admin_line_user_id(line_user_id)})
