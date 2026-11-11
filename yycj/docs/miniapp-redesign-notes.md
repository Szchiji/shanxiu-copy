# 月影车姬 Mini App Redesign Notes

## Schema

- New tables via `create_all`: `site_settings`, `homepage_pins`, `reviews`
- Existing table columns ensured on startup in `bot/db.py` (`IF NOT EXISTS`):
  - `users.role`
  - `lamps.district`, `approx_lat`, `approx_lng`, `approx_label`, `media`, `publisher_role`
- Manual ALTER (if auto-ensure unavailable):

```sql
ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(16);
ALTER TABLE lamps ADD COLUMN IF NOT EXISTS district VARCHAR(64);
ALTER TABLE lamps ADD COLUMN IF NOT EXISTS approx_lat DOUBLE PRECISION;
ALTER TABLE lamps ADD COLUMN IF NOT EXISTS approx_lng DOUBLE PRECISION;
ALTER TABLE lamps ADD COLUMN IF NOT EXISTS approx_label VARCHAR(128);
ALTER TABLE lamps ADD COLUMN IF NOT EXISTS media JSONB DEFAULT '[]'::jsonb;
ALTER TABLE lamps ADD COLUMN IF NOT EXISTS publisher_role VARCHAR(16);
```

## TODO

- Binary media upload from Telegram WebApp (current: URL / file_id, max 9)
- Message inbox UI beyond Bot handoff
- Pin drag-reorder persistence UX polish

## Webhook

Lifespan does **not** call `delete_webhook` (rolling deploy safe).
