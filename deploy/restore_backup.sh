#!/bin/bash
# Відновлення smartbess з бекапу (deploy/backup_db.sh): БД Postgres/TimescaleDB і/або
# папка data/ (історія з 2021, кеші, моделі). Запускати на VPS від root.
#
#   ./restore_backup.sh --list                     показати доступні бекапи
#   ./restore_backup.sh                            найсвіжіші БД + data/ з Google Drive
#   ./restore_backup.sh --stamp 20261007_073950    конкретний бекап
#   ./restore_backup.sh --db-only | --data-only    лише одна частина
#   ./restore_backup.sh --from local               з /root/backups/smartbess-db, не з Drive
#   ./restore_backup.sh --target-db test_restore --db-only
#                                                  перевірка в окрему БД, робоча не чіпається
#   --yes                                          без підтвердження
#
# Перед відновленням у робочу БД/data/ робиться страхувальна копія поточного стану
# (pre_restore_*), платформа зупиняється на час відновлення й запускається знову.
# Якщо сервер новий: встановити docker, rclone (apt install rclone), скопіювати
# /root/.config/rclone/rclone.conf (або заново `rclone config` → remote "gdrive"),
# розгорнути проєкт у /root/energo/smartbess-ems, `docker compose up -d smartbess-db`,
# далі цей скрипт.
set -euo pipefail

PROJECT=/root/energo/smartbess-ems
LOCAL=/root/backups/smartbess-db
REMOTE=gdrive:smartbess-backups
DB_CONTAINER=smartbess-db
APP_SERVICE=smartbess-platform
FROM=gdrive
STAMP=""
DO_DB=1
DO_DATA=1
TARGET_DB=smartbess
YES=0
LIST=0

while [ $# -gt 0 ]; do
    case "$1" in
        --list) LIST=1 ;;
        --from) FROM="$2"; shift ;;
        --stamp) STAMP="$2"; shift ;;
        --db-only) DO_DATA=0 ;;
        --data-only) DO_DB=0 ;;
        --target-db) TARGET_DB="$2"; shift ;;
        --yes) YES=1 ;;
        -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
        *) echo "Невідомий параметр: $1"; exit 2 ;;
    esac
    shift
done

list_files() {
    if [ "$FROM" = gdrive ]; then rclone lsf "$REMOTE/"; else ls -1 "$LOCAL"; fi
}

if [ "$LIST" = 1 ]; then
    echo "Бекапи ($FROM):"
    list_files | grep -E '^smartbess_(data_)?[0-9]{8}_[0-9]{6}\.(sql|tar)\.gz$' | sort
    exit 0
fi

ALL=$(list_files)
pick() {  # $1 = префікс файлу, $2 = розширення
    if [ -n "$STAMP" ]; then
        echo "$ALL" | grep -x "$1${STAMP}$2" || true
    else
        echo "$ALL" | grep -E "^$1[0-9]{8}_[0-9]{6}$2\$" | sort | tail -1
    fi
}
DB_FILE=""; DATA_FILE=""
[ "$DO_DB" = 1 ] && DB_FILE=$(pick smartbess_ .sql.gz)
[ "$DO_DATA" = 1 ] && DATA_FILE=$(pick smartbess_data_ .tar.gz)
[ "$DO_DB" = 1 ] && [ -z "$DB_FILE" ] && { echo "Не знайдено дамп БД${STAMP:+ за $STAMP} ($FROM)"; exit 1; }
[ "$DO_DATA" = 1 ] && [ -z "$DATA_FILE" ] && { echo "Не знайдено архів data/${STAMP:+ за $STAMP} ($FROM)"; exit 1; }

TOUCHES_PROD=0
{ [ "$DO_DB" = 1 ] && [ "$TARGET_DB" = smartbess ]; } && TOUCHES_PROD=1
[ "$DO_DATA" = 1 ] && TOUCHES_PROD=1

echo "Джерело: $FROM"
[ "$DO_DB" = 1 ] && echo "  БД:    $DB_FILE  ->  база '$TARGET_DB' (ПОВНІСТЮ замінюється)"
[ "$DO_DATA" = 1 ] && echo "  data/: $DATA_FILE  ->  $PROJECT/data (поточна папка буде перейменована)"
if [ "$YES" != 1 ]; then
    read -r -p "Продовжити? [yes/N] " ans
    [ "$ans" = yes ] || { echo "Скасовано."; exit 1; }
fi

WORK=$(mktemp -d /root/restore_XXXXXX)
trap 'rm -rf "$WORK"' EXIT
fetch() {
    if [ "$FROM" = gdrive ]; then rclone copy "$REMOTE/$1" "$WORK/"; else cp "$LOCAL/$1" "$WORK/"; fi
    gzip -t "$WORK/$1"
}
[ -n "$DB_FILE" ] && fetch "$DB_FILE"
[ -n "$DATA_FILE" ] && fetch "$DATA_FILE"

NOW=$(date +%Y%m%d_%H%M%S)
psql_admin() { docker exec -i -u postgres "$DB_CONTAINER" psql -U postgres -v ON_ERROR_STOP=1 -q "$@"; }

if [ "$TOUCHES_PROD" = 1 ]; then
    echo "Страхувальна копія поточного стану..."
    if [ "$DO_DB" = 1 ] && psql_admin -tAc "select 1 from pg_database where datname='smartbess'" | grep -q 1; then
        docker exec -u postgres "$DB_CONTAINER" pg_dump -U postgres -d smartbess 2>/dev/null | gzip > "$LOCAL/pre_restore_$NOW.sql.gz"
        echo "  $LOCAL/pre_restore_$NOW.sql.gz"
    fi
    echo "Зупиняю $APP_SERVICE..."
    (cd "$PROJECT" && docker compose stop "$APP_SERVICE")
fi

if [ -n "$DB_FILE" ]; then
    echo "Відновлення БД '$TARGET_DB'..."
    psql_admin -d postgres -c "select pg_terminate_backend(pid) from pg_stat_activity where datname='$TARGET_DB' and pid<>pg_backend_pid();" >/dev/null
    psql_admin -d postgres -c "drop database if exists \"$TARGET_DB\";"
    psql_admin -d postgres -c "create database \"$TARGET_DB\";"
    psql_admin -d "$TARGET_DB" -c "create extension if not exists timescaledb;"
    # TimescaleDB: pre_restore/post_restore обов'язкові для коректного відновлення гіпертаблиць.
    psql_admin -d "$TARGET_DB" -c "select timescaledb_pre_restore();" >/dev/null
    ERRLOG="$WORK/psql_errors.log"
    gunzip -c "$WORK/$DB_FILE" | docker exec -i -u postgres "$DB_CONTAINER" psql -U postgres -d "$TARGET_DB" -q >/dev/null 2>"$ERRLOG" || true
    psql_admin -d "$TARGET_DB" -c "select timescaledb_post_restore();" >/dev/null
    NERR=$(grep -c "ERROR" "$ERRLOG" || true)
    echo "  помилок psql: $NERR (дрібні про вже наявне розширення/схему — норма)"
    [ "$NERR" -gt 0 ] && grep "ERROR" "$ERRLOG" | sort | uniq -c | head -10
    echo "  рядків: assets=$(psql_admin -d "$TARGET_DB" -tAc 'select count(*) from assets') market_bids=$(psql_admin -d "$TARGET_DB" -tAc 'select count(*) from market_bids') charge_discharge_plans=$(psql_admin -d "$TARGET_DB" -tAc 'select count(*) from charge_discharge_plans')"
fi

if [ -n "$DATA_FILE" ]; then
    echo "Відновлення data/..."
    if [ -d "$PROJECT/data" ]; then
        mv "$PROJECT/data" "$PROJECT/data.before_restore_$NOW"
        echo "  поточна папка -> $PROJECT/data.before_restore_$NOW"
    fi
    tar xzf "$WORK/$DATA_FILE" -C "$PROJECT"
    echo "  історія: $(($(wc -l < "$PROJECT/data/historical_data_merged.csv") - 1)) годинних рядків"
fi

if [ "$TOUCHES_PROD" = 1 ]; then
    echo "Запускаю $APP_SERVICE..."
    (cd "$PROJECT" && docker compose start "$APP_SERVICE")
fi
echo "Готово."
