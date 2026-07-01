# LINE LIFF 會員抽獎系統進度紀錄

日期：2026-06-30

## 目前狀態

系統已完成 V1 正式版架構，目前使用測試 LINE Login Channel 驗證流程。未來切換到鮭魚商店 LINE 時，只需要修改 `.env`。

## 已完成

- 建立正式專案目錄：`line-lottery/`
- LINE Login Channel ID、Channel Secret、LIFF ID 均由 `.env` 管理
- Python、HTML、CSS、JavaScript 沒有硬寫 LINE ID 或 LIFF ID
- 建立 Flask API Blueprint：
  - `api/member.py`
  - `api/lottery.py`
  - `api/history.py`
  - `api/admin.py`
- 建立 service：
  - `services/line_login.py`
  - `services/lottery_service.py`
- 建立前端頁面：
  - `templates/lottery.html`
  - `templates/history.html`
- 建立靜態檔案：
  - `static/css/style.css`
  - `static/js/app.js`
  - `static/images/`
- SQLite 正式資料表：
  - `members`
  - `prizes`
  - `lottery_records`
  - `daily_spin`
  - `member_spin_limits`
  - `prize_serials`
  - `app_settings`
- 預設獎項 6 筆已建立
- 每日抽獎限制使用 `daily_spin(line_user_id, date, count)`
- 全體每日次數由 `app_settings.default_daily_spin_limit` 控制
- 單一會員每日次數與封鎖由 `member_spin_limits` 控制
- 抽獎結果由後端決定，前端不能指定獎項
- 中獎紀錄可依 `line_user_id` 查詢
- 轉盤已改成 SVG 扇形，獎項顯示由 `/api/lottery/prizes` 讀取
- 機率由 `prizes.weight` 控制
- 新增獎品序號表 `prize_serials`
- 支援獎項設定 `requires_serial`
- 抽中需要序號的獎項時，系統會自動指派序號並登記 `line_user_id`
- 支援序號勾選已兌換：`status = redeemed`
- 新增 Google Sheet 控制源：
  - 可從 Google Sheet 同步獎品
  - 可同步數量、機率/權重、啟用狀態、序號
  - 可手動同步
  - 可用 `.env` 開啟前台自動同步

## 已測試

- `python -m compileall -f .` 通過
- `/health` 回傳 `ok: true`
- `/lottery` 回傳 HTTP 200
- `/api/lottery/prizes` 可回傳獎項、權重與機率
- API 流程測試通過：
  - 會員同步成功
  - 第一次抽獎成功
  - 同一天超過限制會回傳「今日已抽過」
  - 歷史紀錄可查詢
- 管理 API 測試通過：
  - 可查詢獎項與機率
  - 可設定全域每日抽獎次數
  - 可設定單一會員每日抽獎次數
  - 可匯入獎品序號
  - 抽中序號獎品時會把序號改成 `assigned`
  - 序號會記錄抽中的 LINE userId 與 `lottery_record_id`
- Google Sheet 同步程式已加入
- Google Sheet 已改用 Service Account，不需要公開 CSV
- 目標分頁已更新為 `轉盤`
- `轉盤` 分頁已寫入中文表頭
- 已寫入正式獎項資料列與 214 組亂序 10 碼序號
- Service Account 讀取 `轉盤` 分頁成功
- Google Sheet 同步回 SQLite 成功
- 抽中序號獎品後會回寫試算表：
  - `序號狀態` 改成 `已抽中`
  - 寫入 `抽中會員ID`
  - 寫入 `抽獎紀錄ID`
  - 寫入 `抽中時間`

## 目前說明

轉盤已修正為 SVG，不再使用原本容易偏移的 CSS conic-gradient label 定位。

抽獎次數控制方式：

- 全體預設：`PATCH /api/admin/settings/daily-spin-limit`
- 單一會員：`PUT /api/admin/members/<line_user_id>/spin-limit`

機率控制方式：

- 調整 `prizes.weight`
- 查詢 `/api/admin/prizes` 可看到 `probabilityPercent`

序號控制方式：

- 匯入序號到 `prize_serials`
- 獎項設定 `requiresSerial: true`
- 抽中時自動把序號從 `available` 改成 `assigned`
- 後台或出貨單確認後可改成 `redeemed`

Google Sheet 控制方式：

- `.env` 已加入 `GOOGLE_SHEET_ID` 與 `GOOGLE_SHEET_GID`
- `.env` 已加入 `GOOGLE_SHEET_NAME=轉盤`
- `.env` 已加入 `GOOGLE_SERVICE_ACCOUNT_FILE`
- Service Account：`salmon-lottery-reader@bot-line-452013.iam.gserviceaccount.com`
- 初始化表頭 API：`POST /api/admin/google-sheet/setup`
- 管理 API：`POST /api/admin/google-sheet/sync`
- 狀態 API：`GET /api/admin/google-sheet`
- 前台自動同步可用 `SHEET_SYNC_ENABLED=true` 開啟

目前獎品與機率：

```text
COUPON30    30元折價券      18.78%，100 組序號
COUPON170   170元折價券     15.02%，80 組序號
COUPON990   990元折價券     3.76%，20 組序號
COUPON1690  1690元折價券    1.88%，10 組序號
COUPON3280  3280元折價券    0.56%，3 組序號
IPHONE16    iPhone 16       0%，1 組序號
NONE        銘謝惠顧        60%
```

## 下一步

1. 用手機重新開 LIFF，確認新 SVG 轉盤畫面是否正常。
2. 在 `轉盤` 分頁調整獎品、機率、數量或序號。
3. 呼叫 `POST /api/admin/google-sheet/sync` 同步。
4. 若要自動同步，將 `.env` 的 `SHEET_SYNC_ENABLED=true`。
5. 補正式管理後台 UI，讓你不用打 curl 也能調整次數、機率、序號與兌換狀態。
