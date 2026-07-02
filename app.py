from flask import Flask, jsonify, redirect, render_template

from api.admin import admin_bp
from api.history import history_bp
from api.lottery import lottery_bp
from api.member import member_bp
from config import Config, security_warnings, validate_runtime_config
from services.database import database_health, database_mode
from services.google_sheet_service import get_authorized_session, get_sheet_title, service_account_summary, sync_from_google_sheet
from services.lottery_service import init_db
from services.operation_log_service import write_operation_log
from services.lottery_service import now_iso
from services.scheduled_backup_service import start_daily_backup_scheduler


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
            write_operation_log(
                "google_sheet_startup_sync",
                level="error",
                message="Google Sheet startup sync failed",
                payload={"error": str(error)},
            )

    app.register_blueprint(member_bp)
    app.register_blueprint(lottery_bp)
    app.register_blueprint(history_bp)
    app.register_blueprint(admin_bp)
    start_daily_backup_scheduler()

    @app.get("/")
    def home():
        return redirect("/lottery")

    @app.get("/admin")
    def admin_page():
        return render_template("admin.html", liff_id=Config.LIFF_ID)

    @app.get("/health")
    def health():
        missing = validate_runtime_config()
        database_ok, database_error = database_health()
        google_sheet_configured = bool(
            Config.GOOGLE_SHEET_ID
            and (
                Config.GOOGLE_SERVICE_ACCOUNT_JSON
                or Config.GOOGLE_SERVICE_ACCOUNT_FILE
                or Config.GOOGLE_SHEET_CSV_URL
            )
        )
        line_login_configured = bool(Config.LINE_LOGIN_CHANNEL_ID and Config.LINE_LOGIN_CHANNEL_SECRET)
        liff_id_configured = bool(Config.LIFF_ID)
        result = {
            "ok": len(missing) == 0 and database_ok and google_sheet_configured,
            "app": True,
            "database": database_ok,
            "databaseMode": database_mode(),
            "databaseError": database_error,
            "googleSheet": google_sheet_configured,
            "googleSheetConfigured": google_sheet_configured,
            "liffIdConfigured": liff_id_configured,
            "lineLoginConfigured": line_login_configured,
            "missingConfig": missing,
            "securityWarnings": security_warnings(),
            "timestamp": now_iso(),
        }
        return jsonify(result), 200 if result["ok"] else 503

    @app.get("/health/deep")
    def health_deep():
        database_ok, database_error = database_health()
        google_sheet_ok = False
        google_sheet_error = None
        sheet_title = None

        if Config.GOOGLE_SHEET_ID:
            session, error = get_authorized_session()
            if error:
                google_sheet_error = error
            else:
                sheet_title, google_sheet_error = get_sheet_title(session)
                google_sheet_ok = google_sheet_error is None
        else:
            google_sheet_error = "GOOGLE_SHEET_ID is not configured"

        result = {
            "ok": database_ok
            and bool(Config.LIFF_ID)
            and google_sheet_ok,
            "app": True,
            "database": database_ok,
            "databaseMode": database_mode(),
            "databaseError": database_error,
            "googleSheet": google_sheet_ok,
            "googleSheetError": google_sheet_error,
            "googleSheetTitle": sheet_title,
            "serviceAccount": service_account_summary(),
            "liffIdConfigured": bool(Config.LIFF_ID),
            "lineLoginConfigured": bool(Config.LINE_LOGIN_CHANNEL_ID and Config.LINE_LOGIN_CHANNEL_SECRET),
            "missingConfig": validate_runtime_config(),
            "securityWarnings": security_warnings(),
            "timestamp": now_iso(),
        }
        return jsonify(result), 200 if result["ok"] else 503

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
