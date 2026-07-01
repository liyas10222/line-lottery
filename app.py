from flask import Flask, jsonify, redirect, render_template

from api.admin import admin_bp
from api.history import history_bp
from api.lottery import lottery_bp
from api.member import member_bp
from config import Config, validate_runtime_config
from services.google_sheet_service import sync_from_google_sheet
from services.lottery_service import init_db


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    app.json.ensure_ascii = False

    init_db()
    if Config.SHEET_SYNC_ENABLED:
        try:
            sync_from_google_sheet()
        except Exception as error:
            app.logger.warning("Google Sheet startup sync failed: %s", error)

    app.register_blueprint(member_bp)
    app.register_blueprint(lottery_bp)
    app.register_blueprint(history_bp)
    app.register_blueprint(admin_bp)

    @app.get("/")
    def home():
        return redirect("/lottery")

    @app.get("/admin")
    def admin_page():
        return render_template("admin.html", admin_user_ids=sorted(Config.ADMIN_LINE_USER_IDS))

    @app.get("/health")
    def health():
        missing = validate_runtime_config()
        return jsonify({"ok": len(missing) == 0, "missingConfig": missing})

    @app.errorhandler(404)
    def not_found(_error):
        return jsonify({"ok": False, "message": "找不到頁面"}), 404

    @app.errorhandler(500)
    def server_error(_error):
        return jsonify({"ok": False, "message": "伺服器發生錯誤"}), 500

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=Config.PORT, debug=Config.APP_ENV == "development")
