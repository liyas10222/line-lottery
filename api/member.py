from flask import Blueprint, jsonify, request

from config import Config
from services.lottery_service import sync_member


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
    return jsonify({"ok": True, "isAdmin": line_user_id in Config.ADMIN_LINE_USER_IDS})
