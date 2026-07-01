from flask import Blueprint, jsonify, render_template, request

from config import Config
from services.lottery_service import get_history


history_bp = Blueprint("history", __name__)


@history_bp.get("/history")
def page():
    return render_template("history.html", liff_id=Config.LIFF_ID)


@history_bp.get("/api/history")
def list_history():
    line_user_id = request.args.get("lineUserId", "")
    result, status_code = get_history(line_user_id)
    return jsonify(result), status_code


@history_bp.get("/api/lottery/history")
def list_history_alias():
    line_user_id = request.args.get("lineUserId", "")
    result, status_code = get_history(line_user_id)
    return jsonify(result), status_code
