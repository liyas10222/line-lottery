# LINE Lottery Operations

## Database Modes

The app chooses the database backend at startup:

- If `DATABASE_URL` exists, PostgreSQL is used.
- If `DATABASE_URL` is empty, SQLite is used.

Local development can keep:

```env
DATABASE_PATH=lottery.db
DATABASE_URL=
```

Render Persistent Disk SQLite can use:

```env
DATABASE_PATH=/var/data/lottery.db
DATABASE_URL=
```

Render PostgreSQL can use:

```env
DATABASE_URL=<Render PostgreSQL internal database URL>
```

## Deployment Options

### Minimal Change: Render Starter + Persistent Disk

Use this when you want the fastest stable path from the current SQLite app.

- Upgrade the Render web service to a plan that supports Persistent Disk.
- Add a disk mounted at `/var/data`.
- Set `DATABASE_PATH=/var/data/lottery.db`.
- Leave `DATABASE_URL` empty.
- Keep regular backup exports from `/api/admin/backup/export`.

This keeps the current SQLite operational model while protecting data from container rebuilds.

### Recommended Production: Render + PostgreSQL

Use this when traffic and long-term data safety matter more.

- Create a Render PostgreSQL database.
- Set `DATABASE_URL` to the internal database URL.
- Keep `DATABASE_PATH` unset or ignored.
- Deploy the web service normally.
- Export a SQLite backup before switching, then import it into PostgreSQL with `/api/admin/backup/import`.

PostgreSQL is better for concurrent writes, backups, monitoring, and future integrations with members, orders, and coupons.

## Required Production Environment Variables

```env
APP_ENV=production
APP_SECRET_KEY=<long random secret>
ADMIN_API_TOKEN=<long random token>
LINE_LOGIN_CHANNEL_ID=<LINE Login channel id>
LINE_LOGIN_CHANNEL_SECRET=<LINE Login channel secret>
LIFF_ID=<LIFF id>
GOOGLE_SHEET_ID=<sheet id>
GOOGLE_SHEET_GID=<sheet gid>
GOOGLE_SHEET_NAME=轉盤
GOOGLE_SERVICE_ACCOUNT_JSON=<single-line service account JSON>
SHEET_SYNC_ENABLED=true
SHEET_SYNC_INTERVAL_SECONDS=30
```

Do not manually set `PORT` on Render. Render provides it automatically.

## Health Checks

- `/health` is the fast Render health check. It checks app config and database connectivity only.
- `/health/deep` also checks Google Sheet access and can be slower.

## Backup APIs

All backup APIs require `X-Admin-Token`.

Export:

```bash
curl -X POST https://lottery.687tfjog.com/api/admin/backup/export \
  -H "X-Admin-Token: $ADMIN_API_TOKEN" \
  -o line-lottery-backup.json
```

Import:

```bash
curl -X POST https://lottery.687tfjog.com/api/admin/backup/import \
  -H "Content-Type: application/json" \
  -H "X-Admin-Token: $ADMIN_API_TOKEN" \
  --data @line-lottery-backup.json
```

Imports use ignore-on-conflict behavior, so repeating the same import will not duplicate existing rows.

## Rollback

### Code rollback

Revert the Render deployment to the previous Git commit from Render deploy history.

### Data rollback

- SQLite Persistent Disk: restore a previous JSON backup with `/api/admin/backup/import`.
- PostgreSQL: restore from Render PostgreSQL backup or import a JSON backup.

Before risky admin actions, export a backup first.
