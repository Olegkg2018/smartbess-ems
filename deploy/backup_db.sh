#!/bin/bash
# Щоденний бекап БД smartbess (cron на VPS, 03:00 UTC): pg_dump + system_settings.json
# локально (14 днів) і копія на Google Drive (rclone remote "gdrive", scope drive.file —
# бачить лише власні файли; 90 днів). Збій — повідомлення в Telegram, якщо в .env є
# TELEGRAM_BOT_TOKEN і TELEGRAM_CHAT_ID.
set -euo pipefail
DEST=/root/backups/smartbess-db
PROJECT=/root/energo/smartbess-ems
REMOTE=gdrive:smartbess-backups
STAMP=$(date +%Y%m%d_%H%M%S)
DUMP="$DEST/smartbess_$STAMP.sql.gz"
SETTINGS="$DEST/system_settings_$STAMP.json"

notify_failure() {
    local token chat
    token=$(grep -E '^TELEGRAM_BOT_TOKEN=' "$PROJECT/.env" | cut -d= -f2- || true)
    chat=$(grep -E '^TELEGRAM_CHAT_ID=' "$PROJECT/.env" | cut -d= -f2- || true)
    if [ -n "$token" ] && [ -n "$chat" ]; then
        curl -s -m 15 "https://api.telegram.org/bot$token/sendMessage" \
            --data-urlencode "chat_id=$chat" \
            --data-urlencode "text=SmartBESS: бекап БД $STAMP НЕ вдався (рядок $1). Див. $DEST/backup.log" >/dev/null || true
    fi
}
trap 'echo "[$STAMP] FAILED at line $LINENO"; notify_failure $LINENO' ERR

docker exec -u postgres smartbess-db pg_dump -U postgres -d smartbess 2>/dev/null | gzip > "$DUMP"
# Порожній/обірваний дамп не має вважатись бекапом.
[ "$(stat -c %s "$DUMP")" -gt 100000 ]
gzip -t "$DUMP"
cp "$PROJECT/data/system_settings.json" "$SETTINGS"

rclone copy "$DUMP" "$REMOTE/" --retries 5 --low-level-retries 10
rclone copy "$SETTINGS" "$REMOTE/" --retries 5
rclone check "$DEST" "$REMOTE/" --one-way --include "$(basename "$DUMP")" >/dev/null 2>&1

find "$DEST" -name 'smartbess_*.sql.gz' -mtime +14 -delete
find "$DEST" -name 'system_settings_*.json' -mtime +14 -delete
rclone delete "$REMOTE/" --min-age 90d || true

echo "[$STAMP] OK $(stat -c %s "$DUMP") bytes -> $REMOTE"
