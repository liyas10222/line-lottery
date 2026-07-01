import os
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


class Config:
    APP_ENV = os.getenv("APP_ENV", "production")
    SECRET_KEY = os.getenv("APP_SECRET_KEY", "change-this-before-production")
    APP_TIMEZONE = os.getenv("APP_TIMEZONE", "Asia/Taipei")
    DATABASE_PATH = os.getenv("DATABASE_PATH", "lottery.db")
    PORT = int(os.getenv("PORT", "5000"))
    DEFAULT_DAILY_SPIN_LIMIT = int(os.getenv("DEFAULT_DAILY_SPIN_LIMIT", "1"))
    ADMIN_API_TOKEN = os.getenv("ADMIN_API_TOKEN", "")
    ADMIN_LINE_USER_IDS = {
        item.strip()
        for item in os.getenv("ADMIN_LINE_USER_IDS", "").split(",")
        if item.strip()
    }
    GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "")
    GOOGLE_SHEET_GID = os.getenv("GOOGLE_SHEET_GID", "")
    GOOGLE_SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "轉盤")
    GOOGLE_SHEET_CSV_URL = os.getenv("GOOGLE_SHEET_CSV_URL", "")
    GOOGLE_SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "")
    GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    SHEET_SYNC_ENABLED = os.getenv("SHEET_SYNC_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    SHEET_SYNC_INTERVAL_SECONDS = int(os.getenv("SHEET_SYNC_INTERVAL_SECONDS", "300"))

    LINE_LOGIN_CHANNEL_ID = os.getenv("LINE_LOGIN_CHANNEL_ID", "")
    LINE_LOGIN_CHANNEL_SECRET = os.getenv("LINE_LOGIN_CHANNEL_SECRET", "")
    LIFF_ID = os.getenv("LIFF_ID", "")

    JSON_AS_ASCII = False

    @classmethod
    def database_file(cls):
        db_path = Path(cls.DATABASE_PATH)
        if db_path.is_absolute():
            return db_path
        return BASE_DIR / db_path

    @classmethod
    def timezone(cls):
        try:
            return ZoneInfo(cls.APP_TIMEZONE)
        except ZoneInfoNotFoundError:
            return ZoneInfo("UTC")


def validate_runtime_config():
    missing = []
    if not Config.LIFF_ID:
        missing.append("LIFF_ID")

    return missing
