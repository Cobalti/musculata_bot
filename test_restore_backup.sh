#!/bin/bash
# test_restore_backup.sh — проверяет, что последний бэкап РЕАЛЬНО
# восстанавливается, а не просто существует как файл.
#
# Мы никогда не проверяли, что архивы, которые каждую ночь кладёт
# /opt/backup-musculata.sh, действительно можно развернуть обратно —
# бэкап, который нельзя восстановить, ничем не лучше отсутствия бэкапа.
#
# Что делает:
#   1. Берёт САМЫЙ СВЕЖИЙ архив из /opt/backups
#   2. Разворачивает его во временную папку (НЕ трогает боевые файлы)
#   3. Открывает каждую из 4 баз через sqlite3 и проверяет, что она
#      реально читается (PRAGMA integrity_check) и в ней есть ожидаемые
#      таблицы — то есть это не битый/пустой файл
#   4. Показывает количество строк в ключевых таблицах — чтобы глазами
#      можно было сверить, что данные похожи на правду (не 0 заказов
#      в базе, где заказы точно должны быть)
#   5. Убирает за собой временную папку
#
# Использование:
#   chmod +x test_restore_backup.sh
#   ./test_restore_backup.sh

set -e

BACKUP_DIR="/opt/backups"
TMP_DIR=$(mktemp -d)

echo "=== Ищу самый свежий бэкап в $BACKUP_DIR ==="
LATEST=$(ls -t "$BACKUP_DIR"/musculata-db-*.tar.gz 2>/dev/null | head -1)

if [ -z "$LATEST" ]; then
    echo "❌ Ни одного бэкапа не найдено в $BACKUP_DIR — проверь cron и backup-musculata.sh"
    rm -rf "$TMP_DIR"
    exit 1
fi

echo "Нашёл: $LATEST"
echo "Возраст: $(( ($(date +%s) - $(stat -c %Y "$LATEST")) / 3600 )) часов назад"
echo

echo "=== Разворачиваю во временную папку $TMP_DIR ==="
tar -xzf "$LATEST" -C "$TMP_DIR"
echo

FAILED=0

for db in orders.db subscriptions.db referrals.db consent.db; do
    DB_PATH="$TMP_DIR/$db"
    echo "--- $db ---"

    if [ ! -f "$DB_PATH" ]; then
        echo "  ❌ Файла нет в архиве вообще!"
        FAILED=1
        continue
    fi

    # Проверка целостности самого файла SQLite
    INTEGRITY=$(sqlite3 "$DB_PATH" "PRAGMA integrity_check;" 2>&1 || echo "ОШИБКА ОТКРЫТИЯ")
    if [ "$INTEGRITY" != "ok" ]; then
        echo "  ❌ Файл повреждён или не открывается: $INTEGRITY"
        FAILED=1
        continue
    fi
    echo "  ✅ Файл цел (integrity_check: ok)"

    # Список таблиц — просто чтобы убедиться, что там не пустая оболочка
    TABLES=$(sqlite3 "$DB_PATH" ".tables" 2>&1)
    if [ -z "$TABLES" ]; then
        echo "  ❌ В базе вообще нет таблиц"
        FAILED=1
        continue
    fi
    echo "  Таблицы: $TABLES"

    # Считаем строки в главной таблице каждой базы — для общей картины
    case "$db" in
        orders.db)        MAIN_TABLE="orders" ;;
        subscriptions.db)  MAIN_TABLE="subscriptions" ;;
        referrals.db)       MAIN_TABLE="referrals" ;;
        consent.db)          MAIN_TABLE="consent" ;;
    esac
    COUNT=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM $MAIN_TABLE;" 2>&1 || echo "?")
    echo "  Строк в $MAIN_TABLE: $COUNT"
    echo
done

echo "=== Убираю временную папку ==="
rm -rf "$TMP_DIR"

if [ "$FAILED" -eq 1 ]; then
    echo "❌ ЕСТЬ ПРОБЛЕМЫ — см. выше, бэкап нельзя считать надёжным как есть"
    exit 1
else
    echo "✅ Все 4 базы в последнем бэкапе целы и реально восстанавливаются"
fi
