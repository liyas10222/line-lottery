# LINE LIFF 會員抽獎轉盤 V1

這是正式版架構。測試 LINE 與未來鮭魚商店 LINE 的差異只放在 `.env`，Python、HTML、CSS、JavaScript 不需要因為換 LINE Channel 而修改。

## 專案結構

```text
line-lottery/
├─ config.py
├─ app.py
├─ lottery.db
├─ templates/
│  ├─ lottery.html
│  └─ history.html
├─ static/
│  ├─ css/
│  ├─ js/
│  └─ images/
├─ api/
│  ├─ member.py
│  ├─ lottery.py
│  ├─ history.py
│  └─ admin.py
└─ services/
   ├─ line_login.py
   └─ lottery_service.py
```

## 啟動

```bash
cd line-lottery
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

本機網址：

```text
http://127.0.0.1:5000/lottery
```

## 環境設定

`.env` 是唯一需要換線的地方：

```env
LINE_LOGIN_CHANNEL_ID=你的LINE_LOGIN_CHANNEL_ID
LINE_LOGIN_CHANNEL_SECRET=你的LINE_LOGIN_CHANNEL_SECRET
LIFF_ID=你的LIFF_ID
DEFAULT_DAILY_SPIN_LIMIT=0
ADMIN_API_TOKEN=replace-with-a-long-random-admin-token
ADMIN_LINE_USER_IDS=Uxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
GOOGLE_SHEET_ID=1S0mTq8M6jZ9S-ss73mpCy-PGyiSRfWulOsZUXepboGs
GOOGLE_SHEET_GID=1195599148
GOOGLE_SHEET_NAME=轉盤
GOOGLE_SHEET_CSV_URL=
GOOGLE_SERVICE_ACCOUNT_FILE=C:\path\to\service-account.json
SHEET_SYNC_ENABLED=true
SHEET_SYNC_INTERVAL_SECONDS=30
```

未來換成鮭魚商店 LINE 時，只改：

```env
LINE_LOGIN_CHANNEL_ID=鮭魚商店ChannelID
LINE_LOGIN_CHANNEL_SECRET=鮭魚商店ChannelSecret
LIFF_ID=鮭魚商店LIFFID
```

然後重新啟動 Flask。

## LIFF Endpoint

測試時：

```text
https://xxxx.ngrok-free.app/lottery
```

正式時：

```text
https://687tfjog.com/lottery
```

## 前台 API

```text
POST /api/member
GET  /api/lottery?lineUserId=...
POST /api/lottery
GET  /api/history?lineUserId=...
GET  /api/lottery/prizes
```

相容舊路由：

```text
POST /api/member/sync
GET  /api/lottery/status?lineUserId=...
POST /api/lottery/spin
GET  /api/lottery/history?lineUserId=...
```

## 控制抽獎次數

管理頁：

```text
http://127.0.0.1:5000/admin
```

只有 `.env` 的 `ADMIN_LINE_USER_IDS` 內列出的 LINE userId 會在 LIFF 抽獎頁看到管理入口。管理 API 仍需要 `ADMIN_API_TOKEN`，所以正式上線前請把預設 token 換成長隨機字串。

管理頁目前可做：

```text
查詢會員今日已抽次數
指定單一 LINE userId 每日可抽幾次
封鎖或解除封鎖單一 LINE userId
重製單一 LINE userId 今日抽獎次數
查看 Google Sheet 同步狀態
立即同步 Google Sheet
查看目前獎池與可用序號數
```

全體會員預設可抽次數存在 `app_settings.default_daily_spin_limit`，預設值由 `.env` 的 `DEFAULT_DAILY_SPIN_LIMIT` 建立；正式活動預設應為 `0`，由訂單、活動或管理員補發。

更新全體會員預設可抽次數：

```bash
curl -X PATCH http://127.0.0.1:5000/api/admin/settings/daily-spin-limit ^
  -H "Content-Type: application/json" ^
  -H "X-Admin-Token: your-admin-token" ^
  -d "{\"dailyLimit\":0}"
```

指定單一會員可抽次數：

```bash
curl -X PUT http://127.0.0.1:5000/api/admin/members/Uxxxxxxxx/spin-limit ^
  -H "Content-Type: application/json" ^
  -H "X-Admin-Token: your-admin-token" ^
  -d "{\"dailyLimit\":3,\"isBlocked\":false,\"note\":\"活動補抽\"}"
```

封鎖單一會員抽獎：

```bash
curl -X PUT http://127.0.0.1:5000/api/admin/members/Uxxxxxxxx/spin-limit ^
  -H "Content-Type: application/json" ^
  -H "X-Admin-Token: your-admin-token" ^
  -d "{\"dailyLimit\":0,\"isBlocked\":true,\"note\":\"暫停抽獎\"}"
```

## 設定機率

機率由 `prizes.weight` 控制。系統會把所有可用獎項的 weight 加總，再換算成實際機率。

預設：

```text
95折優惠券    weight 30 = 30%
97折優惠券    weight 35 = 35%
50元折價券    weight 15 = 15%
100元折價券   weight 8  = 8%
MyCard 30點  weight 2  = 2%
謝謝參與      weight 10 = 10%
```

查詢目前獎項、權重與實際機率：

```bash
curl http://127.0.0.1:5000/api/admin/prizes ^
  -H "X-Admin-Token: your-admin-token"
```

調整某獎項權重：

```bash
curl -X PATCH http://127.0.0.1:5000/api/admin/prizes/5 ^
  -H "Content-Type: application/json" ^
  -H "X-Admin-Token: your-admin-token" ^
  -d "{\"weight\":2,\"isActive\":true}"
```

## 獎品序號與出貨單串接

新增資料表 `prize_serials`，用來承接出貨單或序號分頁資料。

重要欄位：

```text
prize_id
serial_code
status
assigned_line_user_id
lottery_record_id
assigned_at
checked_at
checked_by
source_order_no
source_sheet
source_row
note
```

把某個獎項改成需要序號：

```bash
curl -X PATCH http://127.0.0.1:5000/api/admin/prizes/5 ^
  -H "Content-Type: application/json" ^
  -H "X-Admin-Token: your-admin-token" ^
  -d "{\"requiresSerial\":true}"
```

匯入序號：

```bash
curl -X POST http://127.0.0.1:5000/api/admin/prizes/5/serials ^
  -H "Content-Type: application/json" ^
  -H "X-Admin-Token: your-admin-token" ^
  -d "{\"sourceOrderNo\":\"SHIP-001\",\"sourceSheet\":\"獎品序號\",\"serialCodes\":[\"MC-0001\",\"MC-0002\"]}"
```

抽中需要序號的獎項時，系統會自動把一組 `available` 序號改成 `assigned`，並登記：

```text
assigned_line_user_id
lottery_record_id
assigned_at
```

查詢序號：

```bash
curl "http://127.0.0.1:5000/api/admin/serials?prizeId=5" ^
  -H "X-Admin-Token: your-admin-token"
```

勾選已兌換：

```bash
curl -X PATCH http://127.0.0.1:5000/api/admin/serials/1 ^
  -H "Content-Type: application/json" ^
  -H "X-Admin-Token: your-admin-token" ^
  -d "{\"status\":\"redeemed\",\"checkedBy\":\"admin\"}"
```

## Google Sheet 控制轉盤

目前已支援用 Google Sheet 管理獎品、數量、機率與序號。同步成功後，資料會寫入 SQLite，前台轉盤會從 `/api/lottery/prizes` 讀取最新獎項。

目前建議使用 Service Account，不需要把試算表公開。請將試算表分享給：

```text
salmon-lottery-reader@bot-line-452013.iam.gserviceaccount.com
```

如果只要讀取，給檢視者即可；如果要讓系統建立/更新 `轉盤` 分頁表頭，需給編輯者。

初始化 `轉盤` 分頁與中文表頭：

```bash
curl -X POST http://127.0.0.1:5000/api/admin/google-sheet/setup ^
  -H "X-Admin-Token: your-admin-token"
```

建議欄位名稱：

```text
獎品代碼
獎品名稱
轉盤文字
機率
獎品數量
是否啟用
是否需要序號
序號
序號狀態
抽中會員ID
抽獎紀錄ID
抽中時間
出貨單號
備註
```

範例：

```text
獎品代碼,獎品名稱,轉盤文字,機率,獎品數量,是否啟用,是否需要序號,序號,序號狀態,抽中會員ID,抽獎紀錄ID,抽中時間,出貨單號,備註
COUPON30,30元折價券,30元,18.78,100,是,是,XXXXXXXXXX,可抽,,,,,
COUPON170,170元折價券,170元,15.02,80,是,是,XXXXXXXXXX,可抽,,,,,
IPHONE16,iPhone 16,iPhone 16,0,1,是,是,XXXXXXXXXX,可抽,,,,,
NONE,銘謝惠顧,銘謝,60,,是,否,,,,,,,
```

說明：

```text
獎品代碼：唯一值，用來更新同一個獎品
機率：實際是 weight 權重，所有啟用且可用獎項加總後換算百分比
獎品數量：空白代表不限量
是否需要序號：是 / 否
序號：可一列一組，也可同一格用換行或逗號分隔多組
序號狀態：可抽 / 已抽中 / 已兌換
```

目前正式獎品代碼：

```text
COUPON30    30元折價券
COUPON170   170元折價券
COUPON990   990元折價券
COUPON1690  1690元折價券
COUPON3280  3280元折價券
IPHONE16    iPhone 16
NONE        銘謝惠顧
```

手動同步：

```bash
curl -X POST http://127.0.0.1:5000/api/admin/google-sheet/sync ^
  -H "X-Admin-Token: your-admin-token"
```

查詢同步狀態：

```bash
curl http://127.0.0.1:5000/api/admin/google-sheet ^
  -H "X-Admin-Token: your-admin-token"
```

自動同步：

```env
SHEET_SYNC_ENABLED=true
SHEET_SYNC_INTERVAL_SECONDS=30
```

啟用後，前台讀取轉盤獎項或抽獎前，系統會依間隔自動嘗試同步 Google Sheet。同步成功後，轉盤會反映最新資料。

多人同時抽獎時，防止同一組序號被抽到不是靠 Google Sheet，而是靠 SQLite 的交易鎖。抽獎 API 會先在資料庫內把一組 `available` 序號改成 `assigned`，寫入中獎紀錄並提交後，再回寫 Google Sheet 的「序號狀態、抽中會員ID、抽獎紀錄ID、抽中時間」。即使 Sheet 回寫短暫失敗，資料庫裡已 assigned 的序號也不會再進入獎池。

Google Sheet 同步既會更新獎品、機率、啟用狀態與可控序號狀態，也會更新每組序號所在的 Sheet 列號；但不會把資料庫已抽中的序號降回可抽，避免管理表與即時抽獎併發時發生重複發獎。

## 移植流程

1. 建立鮭魚商店 LINE Login Channel。
2. 建立鮭魚商店 LIFF。
3. 更新 `.env` 的 `LINE_LOGIN_CHANNEL_ID`、`LINE_LOGIN_CHANNEL_SECRET`、`LIFF_ID`。
4. 將 LIFF Endpoint URL 改成正式網域，例如 `https://687tfjog.com/lottery`。
5. 重新啟動 Flask。

除此之外，不需要修改 Python、HTML、CSS 或 JavaScript。
