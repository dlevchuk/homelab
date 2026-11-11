#!/bin/sh
# Раз на хвилину пише "<boot_id> <unix_ts>" у heartbeat-файл.
# Пишемо тільки коли годинник синхронізований (на Pi без RTC час після буту хибний).
# На першому записі нового буту старий файл копіюється в heartbeat.prev,
# щоб raspberry_outage.py міг прочитати heartbeat попереднього буту.

DIR="${HEARTBEAT_DIR:-/var/lib/raspberry-outage}"
F="$DIR/heartbeat"

[ "$(timedatectl show -p NTPSynchronized --value 2>/dev/null)" = "yes" ] || exit 0

BID=$(cat /proc/sys/kernel/random/boot_id)

if [ -f "$F" ] && ! grep -q "^$BID " "$F"; then
    cp -f "$F" "$F.prev"
fi

echo "$BID $(date +%s)" > "$F"
sync "$F"
