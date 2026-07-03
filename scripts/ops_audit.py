import argparse
import json
import os
import shutil
import sys
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


PRIZE_DEFS = [
    ("銘謝惠顧", "THANKS", "銘謝惠顧", 88.6, 999999, False, True, 0),
    ("神秘小禮物", "MYSTERY_GIFT", "神秘小禮物", 2.0, 20, True, True, 20),
    ("30元折價券", "COUPON30", "30元", 8.0, 500, True, True, 500),
    ("原價170商品兌換券", "COUPON170", "170兌換", 0.6, 120, True, True, 120),
    ("原價990商品兌換券", "COUPON990", "990兌換", 0.5, 120, True, True, 120),
    ("原價1690商品兌換券", "COUPON1690", "1690兌換", 0.2, 80, True, True, 80),
    ("原價3290商品兌換券", "COUPON3280", "3290兌換", 0.1, 50, True, True, 50),
    ("AirPods Pro3", "AIRPODS_PRO3", "AirPods", 0.0, 1, True, True, 1),
    ("Nintendo 任天堂 Switch 2 瑪利歐賽車世界組合包", "SWITCH2_MARIOKART", "Switch 2", 0.0, 1, True, True, 1),
    ("iPhone 17 256GB", "IPHONE17_256", "iPhone 17", 0.0, 1, True, True, 1),
]


ADMIN_TOKEN = "audit-token"
ADMIN_LINE_USER_ID = "UADMIN_AUDIT"
ADMIN_HEADERS = {"X-Admin-Token": ADMIN_TOKEN, "X-Admin-Line-User-Id": ADMIN_LINE_USER_ID}


def configure_isolated_env(tmpdir):
    os.environ["DATABASE_PATH"] = str(Path(tmpdir) / "ops_audit.db")
    os.environ["DATABASE_URL"] = ""
    os.environ["APP_ENV"] = "production"
    os.environ["APP_SECRET_KEY"] = "ops-audit-secret"
    os.environ["ADMIN_API_TOKEN"] = ADMIN_TOKEN
    os.environ["ADMIN_LINE_USER_IDS"] = ADMIN_LINE_USER_ID
    os.environ["LIFF_ID"] = "ops-audit-liff-id"
    os.environ["SHEET_SYNC_ENABLED"] = "false"
    os.environ["AUTO_BACKUP_ENABLED"] = "false"
    os.environ["GOOGLE_SHEET_ID"] = ""
    os.environ["GOOGLE_SHEET_GID"] = ""
    os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"] = ""
    os.environ["GOOGLE_SERVICE_ACCOUNT_FILE"] = ""
    os.environ["GOOGLE_SHEET_CSV_URL"] = ""


def build_context():
    from app import app

    app.config["TESTING"] = True
    return app.test_client()


def api_json(response):
    return response.status_code, response.get_json(silent=True)


def get_services():
    from services.database import get_db
    from services.lottery_service import now_iso, spin_lottery, sync_member

    return get_db, now_iso, spin_lottery, sync_member


def reset_tables():
    get_db, _, _, _ = get_services()
    with get_db() as db:
        db.execute("UPDATE prize_serials SET lottery_record_id = NULL")
        db.execute("UPDATE lottery_records SET prize_serial_id = NULL")
        for table_name in [
            "operation_logs",
            "daily_spin",
            "member_spin_limits",
            "admin_line_users",
            "prize_serials",
            "lottery_records",
            "members",
            "prizes",
        ]:
            db.execute(f"DELETE FROM {table_name}")
        db.commit()


def seed_prizes(big_weight=0.0, mystery_serials=20, coupon30_serials=500):
    get_db, now_iso, _, _ = get_services()
    reset_tables()
    timestamp = now_iso()
    with get_db() as db:
        prize_ids = {}
        for name, code, short_label, weight, stock, requires_serial, active, serial_count in PRIZE_DEFS:
            if code in {"AIRPODS_PRO3", "SWITCH2_MARIOKART", "IPHONE17_256"}:
                weight = big_weight
            if code == "MYSTERY_GIFT":
                stock = mystery_serials
                serial_count = mystery_serials
            if code == "COUPON30":
                stock = coupon30_serials
                serial_count = coupon30_serials

            cursor = db.execute_insert(
                """
                INSERT INTO prizes
                    (name, code, short_label, weight, stock, requires_serial, is_active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (name, code, short_label, weight, stock, 1 if requires_serial else 0, 1 if active else 0, timestamp, timestamp),
            )
            prize_id = cursor.lastrowid
            prize_ids[code] = prize_id

            for index in range(1, serial_count + 1):
                db.execute(
                    """
                    INSERT INTO prize_serials
                        (prize_id, serial_code, status, created_at, updated_at)
                    VALUES (?, ?, 'available', ?, ?)
                    """,
                    (prize_id, f"{code}-{index:05d}", timestamp, timestamp),
                )
        db.commit()
    return prize_ids


def sync_customer(client, line_user_id, display_name):
    return api_json(
        client.post(
            "/api/member/sync",
            json={"lineUserId": line_user_id, "displayName": display_name, "pictureUrl": "https://example.com/avatar.png"},
        )
    )


def grant_spins(client, line_user_id, remaining, blocked=False, note="ops audit"):
    return api_json(
        client.put(
            f"/api/admin/members/{line_user_id}/spin-limit",
            headers=ADMIN_HEADERS,
            json={"remainingSpins": remaining, "isBlocked": blocked, "note": note},
        )
    )


def lottery_status(client, line_user_id):
    return api_json(client.get(f"/api/lottery/status?lineUserId={line_user_id}"))


def spin(client, line_user_id, display_name="顧客A"):
    return api_json(client.post("/api/lottery/spin", json={"lineUserId": line_user_id, "displayName": display_name}))


def bulk_draw(client, line_user_id, count=10, display_name="顧客A"):
    return api_json(
        client.post(
            "/api/lottery/draw-bulk",
            json={"lineUserId": line_user_id, "displayName": display_name, "count": count, "skipAnimation": True},
        )
    )


def history(client, line_user_id):
    return api_json(client.get(f"/api/history?lineUserId={line_user_id}"))


def admin_get(client, path, headers=None):
    return api_json(client.get(path, headers=headers or ADMIN_HEADERS))


def admin_post(client, path, payload):
    return api_json(client.post(path, headers=ADMIN_HEADERS, json=payload))


def compact_check(check):
    name, status_code, data = check
    ok = bool(data and data.get("ok"))
    output = {"name": name, "status": status_code, "ok": ok}

    if name == "customer_default_status":
        output.update({"remaining": data.get("remaining"), "canSpin": data.get("canSpin"), "totalLimit": data.get("totalLimit")})
    elif name == "customer_spin_without_quota":
        output["message"] = data.get("message")
    elif name == "customer_single_spin":
        output.update({"prize": data.get("prize"), "sheetWriteback": data.get("sheetWriteback")})
    elif name == "customer_bulk_10":
        output.update({"successCount": data.get("successCount"), "failureCount": data.get("failureCount"), "remainingSpins": data.get("remainingSpins")})
    elif name == "customer_status_after_11":
        output.update({"remaining": data.get("remaining"), "used": data.get("used")})
    elif name == "customer_history":
        records = data.get("records", []) if data else []
        output.update(
            {
                "recordCount": len(records),
                "newestFirst": all(records[index]["createdAt"] >= records[index + 1]["createdAt"] for index in range(len(records) - 1)),
            }
        )
    elif name == "admin_member_list":
        members = data.get("members", []) if data else []
        output.update(
            {
                "memberCount": len(members),
                "remaining": members[0].get("remaining") if members else None,
                "usedCount": members[0].get("usedCount") if members else None,
            }
        )
    elif name.startswith("admin_records"):
        output["recordCount"] = len(data.get("records", [])) if data else None
    elif name.startswith("csv"):
        output.update({"summary": data.get("summary") if data else None, "message": data.get("message") if data else None})
    elif name == "add_serials_api":
        output.update({"created": data.get("created"), "skipped": data.get("skipped"), "sheetWriteback": data.get("sheetWriteback")})
    elif name in {"admin_missing_token", "admin_token_only", "admin_wrong_line_id", "blocked_member_spin"}:
        output["message"] = data.get("message") if data else None
    elif name in {"delete_member", "admin_user_add", "admin_user_delete"}:
        output.update({"message": data.get("message") if data else None, "deletedMembers": data.get("deletedMembers") if data else None})
    return output


def run_role_play(client):
    seed_prizes()
    checks = []

    checks.append(("customer_sync", *sync_customer(client, "UCUST_AUDIT_A", "顧客A")))
    checks.append(("customer_default_status", *lottery_status(client, "UCUST_AUDIT_A")))
    checks.append(("customer_spin_without_quota", *spin(client, "UCUST_AUDIT_A")))

    checks.append(("admin_grant_12", *grant_spins(client, "UCUST_AUDIT_A", 12)))
    checks.append(("customer_status_after_grant", *lottery_status(client, "UCUST_AUDIT_A")))
    checks.append(("customer_single_spin", *spin(client, "UCUST_AUDIT_A")))
    checks.append(("customer_bulk_10", *bulk_draw(client, "UCUST_AUDIT_A", 10)))
    checks.append(("customer_status_after_11", *lottery_status(client, "UCUST_AUDIT_A")))
    checks.append(("customer_history", *history(client, "UCUST_AUDIT_A")))

    checks.append(("admin_member_list", *admin_get(client, "/api/admin/members?limit=20&q=顧客A")))
    checks.append(("admin_records_all", *admin_get(client, "/api/admin/lottery-records?limit=20&q=顧客A")))
    checks.append(("admin_records_won", *admin_get(client, "/api/admin/lottery-records?limit=20&q=顧客A&status=won")))
    checks.append(("admin_records_not_won", *admin_get(client, "/api/admin/lottery-records?limit=20&q=顧客A&status=not_won")))

    sync_customer(client, "UCUST_AUDIT_BLOCK", "封鎖測試")
    grant_spins(client, "UCUST_AUDIT_BLOCK", 2, blocked=True)
    checks.append(("blocked_member_spin", *spin(client, "UCUST_AUDIT_BLOCK", "封鎖測試")))
    checks.append(("delete_member", *api_json(client.delete("/api/admin/members/UCUST_AUDIT_BLOCK", headers=ADMIN_HEADERS))))

    checks.append(("admin_user_add", *admin_post(client, "/api/admin/admin-users", {"lineUserId": "UADMIN_HELPER_AUDIT", "displayName": "協助管理員", "note": "audit"})))
    checks.append(("admin_user_delete", *api_json(client.delete("/api/admin/admin-users/UADMIN_HELPER_AUDIT", headers=ADMIN_HEADERS))))

    csv_bad = (
        "\ufeffprize_code,prize_name,short_label,weight,stock,requires_serial,is_active,serial_code\n"
        "AUDIT100,測試100券,100券,1.5,2,true,true,AUDIT100-0001\n"
        "AUDIT100,測試100券,100券,1.5,2,true,true,AUDIT100-0001\n"
    )
    checks.append(("csv_preview_duplicate", *admin_post(client, "/api/admin/prizes/import-preview", {"csvText": csv_bad})))

    csv_good = (
        "\ufeffprize_code,prize_name,short_label,weight,stock,requires_serial,is_active,serial_code\n"
        "AUDIT100,測試100券,100券,1.5,2,true,true,AUDIT100-0001\n"
        "AUDIT100,測試100券,100券,1.5,2,true,true,AUDIT100-0002\n"
        "AUDIT_THANKS,測試銘謝,銘謝,1,999,false,true,\n"
    )
    checks.append(("csv_import_valid", *admin_post(client, "/api/admin/prizes/import", {"confirm": "IMPORT_PRIZE_CSV", "csvText": csv_good})))
    checks.append(("csv_import_duplicate_rerun", *admin_post(client, "/api/admin/prizes/import", {"confirm": "IMPORT_PRIZE_CSV", "csvText": csv_good})))

    _, prizes_data = admin_get(client, "/api/admin/prizes")
    audit_prize = next((prize for prize in prizes_data["prizes"] if prize["code"] == "AUDIT100"), None)
    if audit_prize:
        checks.append(
            (
                "add_serials_api",
                *admin_post(
                    client,
                    f"/api/admin/prizes/{audit_prize['id']}/serials",
                    {"serialCodes": ["AUDIT100-0002", "AUDIT100-0003", ""], "note": "audit add serial"},
                ),
            )
        )

    checks.append(("admin_missing_token", *api_json(client.get("/api/admin/members"))))
    checks.append(("admin_token_only", *admin_get(client, "/api/admin/members", headers={"X-Admin-Token": ADMIN_TOKEN})))
    checks.append(("admin_wrong_line_id", *admin_get(client, "/api/admin/members", headers={"X-Admin-Token": ADMIN_TOKEN, "X-Admin-Line-User-Id": "UNOT_ADMIN"})))

    return [compact_check(check) for check in checks]


def run_draws(client, round_name, draws, big_weight=0.0, mystery_serials=20, coupon30_serials=500):
    seed_prizes(big_weight=big_weight, mystery_serials=mystery_serials, coupon30_serials=coupon30_serials)
    line_user_id = f"U{round_name}"
    sync_customer(client, line_user_id, f"{round_name}顧客")
    grant_spins(client, line_user_id, draws)

    counts = Counter()
    serials = []
    failures = []
    for index in range(draws):
        status_code, data = spin(client, line_user_id, f"{round_name}顧客")
        if status_code != 200 or not data or not data.get("ok"):
            failures.append({"index": index + 1, "status": status_code, "data": data})
            break

        prize = data["prize"]
        counts[prize["code"]] += 1
        if prize.get("serialCode"):
            serials.append(prize["serialCode"])

    _, status_data = lottery_status(client, line_user_id)
    _, admin_prizes = admin_get(client, "/api/admin/prizes")
    completed = sum(counts.values())
    big_codes = {"AIRPODS_PRO3", "SWITCH2_MARIOKART", "IPHONE16", "IPHONE17", "IPHONE17_256"}

    return {
        "round": round_name,
        "drawsRequested": draws,
        "drawsCompleted": completed,
        "failures": failures[:3],
        "counts": dict(counts),
        "percent": {code: round(value / max(1, completed) * 100, 3) for code, value in sorted(counts.items())},
        "duplicateSerials": len(serials) - len(set(serials)),
        "remainingSpins": status_data.get("remaining") if status_data else None,
        "bigWins": sum(counts.get(code, 0) for code in big_codes),
        "prizeSnapshot": [
            {
                "code": prize["code"],
                "availableSerials": prize["availableSerials"],
                "assignedSerials": prize["assignedSerials"],
                "probabilityPercent": prize["probabilityPercent"],
                "transferredWeightIn": prize["transferredWeightIn"],
            }
            for prize in (admin_prizes or {}).get("prizes", [])
            if prize["code"] in {"THANKS", "MYSTERY_GIFT", "COUPON30", "AIRPODS_PRO3", "SWITCH2_MARIOKART", "IPHONE17_256"}
        ],
    }


def run_concurrency_race(client):
    _, _, spin_lottery, sync_member = get_services()
    seed_prizes(big_weight=0.0, mystery_serials=0, coupon30_serials=1)

    get_db, now_iso, _, _ = get_services()
    with get_db() as db:
        timestamp = now_iso()
        db.execute("UPDATE prizes SET weight = 0, updated_at = ?", (timestamp,))
        db.execute("UPDATE prizes SET weight = 100, updated_at = ? WHERE code = 'COUPON30'", (timestamp,))
        db.commit()

    for index in range(20):
        sync_member({"lineUserId": f"URACE_{index}", "displayName": f"競速{index}"})
        grant_spins(client, f"URACE_{index}", 1)

    def race_spin(index):
        return spin_lottery({"lineUserId": f"URACE_{index}", "displayName": f"競速{index}"})

    with ThreadPoolExecutor(max_workers=20) as executor:
        raw_results = [future.result() for future in as_completed([executor.submit(race_spin, index) for index in range(20)])]

    counts = Counter()
    serials = []
    errors = []
    for data, status_code in raw_results:
        if status_code != 200 or not data.get("ok"):
            errors.append({"status": status_code, "data": data})
            continue
        prize = data["prize"]
        counts[prize["code"]] += 1
        if prize.get("serialCode"):
            serials.append(prize["serialCode"])

    return {"counts": dict(counts), "serials": serials, "duplicateSerials": len(serials) - len(set(serials)), "errors": errors[:5]}


def run_same_member_race(client):
    _, _, spin_lottery, sync_member = get_services()
    seed_prizes(big_weight=0.0, mystery_serials=0, coupon30_serials=50)

    line_user_id = "USAME_MEMBER_RACE"
    sync_member({"lineUserId": line_user_id, "displayName": "同會員併發"})
    grant_spins(client, line_user_id, 1)

    def race_spin():
        return spin_lottery({"lineUserId": line_user_id, "displayName": "同會員併發"})

    with ThreadPoolExecutor(max_workers=20) as executor:
        raw_results = [future.result() for future in as_completed([executor.submit(race_spin) for _ in range(20)])]

    ok_draws = []
    rejected = []
    errors = []
    for data, status_code in raw_results:
        if status_code != 200:
            errors.append({"status": status_code, "data": data})
            continue
        if data.get("ok"):
            ok_draws.append(data["prize"])
        else:
            rejected.append(data.get("message"))

    _, status_data = lottery_status(client, line_user_id)
    return {
        "successCount": len(ok_draws),
        "rejectedCount": len(rejected),
        "errors": errors[:5],
        "remainingSpins": status_data.get("remaining") if status_data else None,
        "used": status_data.get("used") if status_data else None,
        "serials": [prize.get("serialCode") for prize in ok_draws if prize.get("serialCode")],
    }


def http_json(url, timeout=30):
    request = Request(url, headers={"User-Agent": "line-lottery-ops-audit/1.0"})
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(body)
            except json.JSONDecodeError:
                parsed = None
            return {"ok": 200 <= response.status < 400, "status": response.status, "json": parsed, "length": len(body)}
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        return {"ok": False, "status": error.code, "body": body[:500]}
    except URLError as error:
        return {"ok": False, "status": None, "error": str(error)}


def http_text_summary(url, timeout=30):
    request = Request(url, headers={"User-Agent": "line-lottery-ops-audit/1.0"})
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            title_start = body.find("<title>")
            title_end = body.find("</title>")
            title = body[title_start + 7:title_end] if title_start >= 0 and title_end > title_start else ""
            return {
                "ok": 200 <= response.status < 400,
                "status": response.status,
                "length": len(body),
                "title": title,
                "hasLiffConfig": "LINE_LOTTERY_CONFIG" in body,
                "hasLineLoginText": "LINE 登入" in body,
                "containsRenderUrl": "onrender.com" in body,
            }
    except HTTPError as error:
        return {"ok": False, "status": error.code, "body": error.read().decode("utf-8", errors="replace")[:500]}
    except URLError as error:
        return {"ok": False, "status": None, "error": str(error)}


def run_live_readonly(base_url):
    base_url = base_url.rstrip("/")
    return {
        "health": http_json(f"{base_url}/health"),
        "lotteryPage": http_text_summary(f"{base_url}/lottery"),
        "publicPrizes": http_json(f"{base_url}/api/lottery/prizes"),
        "adminWithoutToken": http_json(f"{base_url}/api/admin/members"),
        "unknownMemberStatus": http_json(f"{base_url}/api/lottery/status?lineUserId=UAUDIT_READONLY_UNKNOWN"),
    }


def evaluate_risks(report):
    risks = []

    role_checks = {item["name"]: item for item in report["roleChecks"]}
    if role_checks.get("admin_token_only", {}).get("status") != 403:
        risks.append({"level": "P0", "message": "Admin API accepts token-only requests without LINE admin identity."})
    if role_checks.get("customer_default_status", {}).get("remaining") != 0:
        risks.append({"level": "P0", "message": "New members do not default to 0 remaining spins."})

    for draw_result in report["largeDraws"]:
        if draw_result["bigWins"] != 0:
            risks.append({"level": "P0", "message": f"Grand prize was drawn in {draw_result['round']}."})
        if draw_result["duplicateSerials"] != 0:
            risks.append({"level": "P0", "message": f"Duplicate serials found in {draw_result['round']}."})
        if draw_result["failures"]:
            risks.append({"level": "P1", "message": f"Draw failures found in {draw_result['round']}."})

    if report["concurrencyRace"]["duplicateSerials"] != 0:
        risks.append({"level": "P0", "message": "Concurrent serial reservation produced duplicate serials."})
    if report["sameMemberRace"]["successCount"] != 1 or report["sameMemberRace"]["used"] != 1:
        risks.append({"level": "P0", "message": "Same member concurrent draw consumed more than one spin."})

    risks.append(
        {
            "level": "P0-hardening",
            "message": "Customer APIs still trust frontend-provided lineUserId. Add LIFF ID token verification before final launch.",
        }
    )
    return risks


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Run isolated customer/admin operations audit for the LINE lottery app.")
    parser.add_argument("--draws", type=int, default=3000, help="Number of draws for the main probability simulation.")
    parser.add_argument("--live-url", default="", help="Optional production URL for read-only health/page checks.")
    parser.add_argument("--keep-db", action="store_true", help="Keep the temporary audit database for manual inspection.")
    args = parser.parse_args()

    tmpdir = tempfile.mkdtemp(prefix="line_lottery_ops_audit_")
    configure_isolated_env(tmpdir)
    client = build_context()

    report = {
        "mode": "isolated",
        "isolatedDatabase": os.environ["DATABASE_PATH"],
        "roleChecks": run_role_play(client),
        "largeDraws": [
            run_draws(client, "FORMAL_MAIN", args.draws, big_weight=0.0, mystery_serials=20, coupon30_serials=500),
            run_draws(client, "BIG_LOCK", max(1000, min(args.draws, 3000)), big_weight=50.0, mystery_serials=20, coupon30_serials=500),
            run_draws(client, "EXHAUST_30", max(800, min(args.draws, 2000)), big_weight=0.0, mystery_serials=5, coupon30_serials=10),
        ],
        "concurrencyRace": run_concurrency_race(client),
        "sameMemberRace": run_same_member_race(client),
    }
    if args.live_url:
        report["liveReadonly"] = run_live_readonly(args.live_url)

    report["risks"] = evaluate_risks(report)

    print(json.dumps(report, ensure_ascii=False, indent=2))

    if args.keep_db:
        print(f"\nKept audit database: {os.environ['DATABASE_PATH']}", file=sys.stderr)
    else:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
