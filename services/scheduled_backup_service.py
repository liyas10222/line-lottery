import logging
import threading
import time
from datetime import datetime, timedelta

from config import Config
from services.backup_service import export_backup_to_file
from services.database import get_db
from services.operation_log_service import write_operation_log


LOGGER = logging.getLogger(__name__)
_scheduler_started = False


def today_string():
    return datetime.now(Config.timezone()).date().isoformat()


def get_last_daily_backup_date():
    with get_db() as db:
        row = db.execute(
            "SELECT value FROM app_settings WHERE key = 'last_daily_backup_date'"
        ).fetchone()
    return row["value"] if row else None


def set_last_daily_backup_date(value):
    timestamp = datetime.now(Config.timezone()).isoformat(timespec="seconds")
    with get_db() as db:
        db.execute(
            """
            INSERT INTO app_settings (key, value, created_at, updated_at)
            VALUES ('last_daily_backup_date', ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (value, timestamp, timestamp),
        )
        db.commit()


def run_daily_backup(reason="scheduled"):
    backup_date = today_string()
    if get_last_daily_backup_date() == backup_date:
        return {"ok": True, "skipped": True, "date": backup_date}

    result, status_code = export_backup_to_file(kind=f"daily-{backup_date}")
    if status_code == 200 and result.get("ok"):
        set_last_daily_backup_date(backup_date)

    write_operation_log(
        "scheduled_daily_backup",
        level="info" if status_code == 200 and result.get("ok") else "error",
        message="Daily backup completed" if status_code == 200 and result.get("ok") else "Daily backup failed",
        payload={"reason": reason, "date": backup_date, "statusCode": status_code, "result": result},
    )
    return result


def seconds_until_next_midnight():
    now = datetime.now(Config.timezone())
    next_midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=5, microsecond=0)
    return max(30, (next_midnight - now).total_seconds())


def scheduler_loop():
    try:
        run_daily_backup(reason="startup-catch-up")
    except Exception as error:
        LOGGER.exception("Startup daily backup failed")
        write_operation_log(
            "scheduled_daily_backup",
            level="error",
            message="Startup daily backup failed",
            payload={"error": str(error)},
        )

    while True:
        time.sleep(seconds_until_next_midnight())
        try:
            run_daily_backup(reason="midnight")
        except Exception as error:
            LOGGER.exception("Scheduled daily backup failed")
            write_operation_log(
                "scheduled_daily_backup",
                level="error",
                message="Scheduled daily backup failed",
                payload={"error": str(error)},
            )


def start_daily_backup_scheduler():
    global _scheduler_started
    if _scheduler_started or not Config.AUTO_BACKUP_ENABLED:
        return False

    thread = threading.Thread(target=scheduler_loop, name="daily-backup-scheduler", daemon=True)
    thread.start()
    _scheduler_started = True
    write_operation_log(
        "scheduled_daily_backup",
        message="Daily backup scheduler started",
        payload={"backupDir": Config.AUTO_BACKUP_DIR},
    )
    return True
