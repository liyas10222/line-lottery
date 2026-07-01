from flask import Blueprint, jsonify, render_template, request

from config import Config
from services.google_sheet_service import maybe_auto_sync_from_google_sheet
from services.lottery_service import get_lottery_status, get_public_prizes, spin_lottery


lottery_bp = Blueprint("lottery", __name__)


@lottery_bp.get("/lottery")
def page():
    return render_template("lottery.html", liff_id=Config.LIFF_ID)


@lottery_bp.get("/api/lottery")
def status():
    line_user_id = request.args.get("lineUserId", "")
    result, status_code = get_lottery_status(line_user_id)
    return jsonify(result), status_code


@lottery_bp.get("/api/lottery/status")
def status_alias():
    line_user_id = request.args.get("lineUserId", "")
    result, status_code = get_lottery_status(line_user_id)
    return jsonify(result), status_code


@lottery_bp.get("/api/lottery/prizes")
def prizes():
    maybe_auto_sync_from_google_sheet()
    result, status_code = get_public_prizes()
    return jsonify(result), status_code


@lottery_bp.post("/api/lottery")
def spin():
    maybe_auto_sync_from_google_sheet()
    payload = request.get_json(silent=True) or {}
    result, status_code = spin_lottery(payload)
    return jsonify(result), status_code


@lottery_bp.post("/api/lottery/spin")
def spin_alias():
    maybe_auto_sync_from_google_sheet()
    payload = request.get_json(silent=True) or {}
    result, status_code = spin_lottery(payload)
    return jsonify(result), status_code
