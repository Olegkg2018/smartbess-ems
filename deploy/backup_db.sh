#!/bin/bash
# Щоденний бекап smartbess (cron на VPS, 01:15 UTC — подалі від нічного перенавчання
# 02:00 Kyiv і 06:00-джоби, що переписують data/): pg_dump + system_settings.json
# (локально 14 днів, Google Drive 90 днів) і архів data/ — історія з 2021, кеші OREE/
# погоди/ENTSO-E/Telegram, моделі (локально 7 днів, Drive 30 днів). rclone remote
# "gdrive", scope drive.file — бачить лише власні файли. Збій — повідомлення в
# Telegram, якщо в .env є TELEGRAM_BOT_TOKEN і TELEGRAM_CHAT_ID.
set -euo pipefail
DEST=/root/backups/smartbess-db
PROJECT=/root/energo/smartbess-ems
REMOTE=gdrive:smartbess-backups
STAMP=$(date +%Y%m%d_%H%M%S)
DUMP="$DEST/smartbess_$STAMP.sql.gz"
SETTINGS="$DEST/system_settings_$STAMP.json"
DATA_ARCHIVE="$DEST/smartbess_data_$STAMP.tar.gz"

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

# Старі ручні .bak/.before_* знімки не потрібні — їх не архівуємо.
# tar exit 1 = "файл змінився під час читання" — не фатально, цілісність перевіряє gzip -t.
rc=0
tar czf "$DATA_ARCHIVE" -C "$PROJECT" \
    --exclude='data/*.bak' --exclude='data/*.before_*' --exclude='data/backup_before_*' data || rc=$?
[ "$rc" -le 1 ]
gzip -t "$DATA_ARCHIVE"
[ "$(tar tzf "$DATA_ARCHIVE" | grep -c '^data/historical_data_merged.csv$')" -eq 1 ]

rclone copy "$DUMP" "$REMOTE/" --retries 5 --low-level-retries 10
rclone copy "$SETTINGS" "$REMOTE/" --retries 5
rclone copy "$DATA_ARCHIVE" "$REMOTE/" --retries 5 --low-level-retries 10
rclone check "$DEST" "$REMOTE/" --one-way --include "$(basename "$DUMP")" --include "$(basename "$DATA_ARCHIVE")" >/dev/null 2>&1

find "$DEST" -name 'smartbess_*.sql.gz' -mtime +14 -delete
find "$DEST" -name 'system_settings_*.json' -mtime +14 -delete
find "$DEST" -name 'smartbess_data_*.tar.gz' -mtime +7 -delete
rclone delete "$REMOTE/" --min-age 30d --include 'smartbess_data_*.tar.gz' || true
rclone delete "$REMOTE/" --min-age 90d || true

echo "[$STAMP] OK db=$(stat -c %s "$DUMP") data=$(stat -c %s "$DATA_ARCHIVE") bytes -> $REMOTE"
