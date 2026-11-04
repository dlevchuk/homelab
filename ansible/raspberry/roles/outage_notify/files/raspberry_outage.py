#!/usr/bin/env python3
"""
Після завантаження Pi чекає на інтернет і шле в Telegram, чому він перезавантажився:
- kernel panic / lockup
- ping-reboot (немає мережі)
- штатне перезавантаження
- нештатне вимкнення (ймовірно світло), з тривалістю і ознаками просідання напруги

Момент втрати живлення = max(heartbeat, останній запис журналу попереднього буту).
Heartbeat пише raspberry-outage-heartbeat.timer (heartbeat.sh) раз на хвилину.

Запускається systemd-юнітом raspberry-outage.service (ansible/raspberry/roles/outage_notify).
Env: BOT_TOKEN, BOT_CHAT_ID, HOST_LABEL (опційно), HEARTBEAT_DIR (опційно).
"""
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHAT_ID = os.environ.get("BOT_CHAT_ID", "")
HOST = os.environ.get("HOST_LABEL") or socket.gethostname()
HEARTBEAT_DIR = os.environ.get("HEARTBEAT_DIR", "/var/lib/raspberry-outage")

RETRY_SEC = 10
MAX_WAIT_SEC = 6 * 3600     # після цього здаємось
SYNC_GRACE_SEC = 10 * 60    # скільки чекати NTP при вже наявному інтернеті

# Ознаки штатного завершення в кінці журналу попереднього буту
CLEAN_SHUTDOWN_RE = re.compile(
    r"(reboot|poweroff|halt)\.target|Reached target .*(Reboot|Power-Off|Halt)",
    re.IGNORECASE,
)
PING_REBOOT_RE = re.compile(r"Rebooting system")  # повідомлення ping-reboot-check.sh
PANIC_RE = re.compile(r"Kernel panic|Oops:|watchdog: .*LOCKUP", re.IGNORECASE)
UNDERVOLT_RE = re.compile(r"Under-voltage detected", re.IGNORECASE)


def log(msg):
    print(msg, flush=True)


def uptime_sec():
    try:
        with open("/proc/uptime") as f:
            return float(f.read().split()[0])
    except (OSError, ValueError, IndexError):
        return 0.0


def have_internet():
    for ip in ("1.1.1.1", "8.8.8.8"):
        try:
            with socket.create_connection((ip, 443), timeout=5):
                return True
        except OSError:
            continue
    return False


def time_synced():
    """Без RTC час після буту може бути хибним, тому дивимось на NTP."""
    try:
        r = subprocess.run(
            ["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
            capture_output=True, text=True, timeout=10,
        )
        return r.returncode != 0 or r.stdout.strip() == "yes"
    except (OSError, subprocess.SubprocessError):
        return True


def journal(*args):
    try:
        r = subprocess.run(
            ["journalctl", "--no-pager", *args],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as e:
        log(f"journalctl error: {e}")
        return ""
    return r.stdout if r.returncode == 0 else ""


def boot_id():
    try:
        with open("/proc/sys/kernel/random/boot_id") as f:
            return f.read().strip()
    except OSError:
        return ""


def read_heartbeat():
    """Heartbeat попереднього буту: перший файл з boot_id, відмінним від поточного."""
    cur = boot_id()
    base = os.path.join(HEARTBEAT_DIR, "heartbeat")
    for path in (base, base + ".prev"):
        try:
            with open(path) as f:
                bid, ts = f.read().split()
            if bid != cur:
                return float(ts)
        except (OSError, ValueError):
            continue
    return None


def previous_boot():
    """Аналіз попереднього буту або None, якщо даних нема."""
    journal_ts = None
    out = journal("-b", "-1", "-n", "1", "-o", "json").strip()
    if out:
        try:
            journal_ts = int(json.loads(out.splitlines()[-1])["__REALTIME_TIMESTAMP"]) / 1e6
        except (ValueError, KeyError, IndexError):
            journal_ts = None

    hb_ts = read_heartbeat()
    candidates = [t for t in (journal_ts, hb_ts) if t]
    if not candidates:
        return None

    last_ts = max(candidates)
    src = "heartbeat" if hb_ts and hb_ts >= (journal_ts or 0) else "журнал"

    tail = journal("-b", "-1", "-n", "100", "-o", "cat")
    kern = journal("-b", "-1", "-k", "-n", "5000", "-o", "cat")
    return {
        "last_ts": last_ts,
        "src": src,
        "clean": bool(CLEAN_SHUTDOWN_RE.search(tail)),
        "ping_reboot": bool(PING_REBOOT_RE.search(tail)),
        "panic": bool(PANIC_RE.search(kern)),
        "undervolt": bool(UNDERVOLT_RE.search(kern)),
    }


def fmt_duration(seconds):
    minutes = max(0, int(seconds // 60))
    days, rem = divmod(minutes, 1440)
    hours, mins = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days} д")
    if days or hours:
        parts.append(f"{hours} год")
    parts.append(f"{mins} хв")
    return " ".join(parts)


def build_message(prev, synced, net_uptime):
    if prev is None:
        return f"ℹ️ {HOST}: завантажився, інтернет є (даних попереднього буту нема)"

    clean = prev["clean"] and not prev["panic"]
    if prev["panic"]:
        head = f"💥 {HOST}: перезавантаження після kernel panic / lockup"
    elif clean and prev["ping_reboot"]:
        head = f"🌐 {HOST}: перезавантаження через відсутність мережі (ping-reboot)"
    elif clean:
        head = f"🔄 {HOST}: штатне перезавантаження"
    else:
        head = f"⚡ {HOST}: нештатне вимкнення (ймовірно світло), інтернет відновлено"

    last = datetime.fromtimestamp(prev["last_ts"]).astimezone()
    lines = [
        head,
        f"Остання активність: {last.strftime('%Y-%m-%d %H:%M:%S %Z')} ({prev['src']})",
    ]

    if synced:
        boot_ts = time.time() - uptime_sec()
        lines.append(f"Pi був вимкнений: {fmt_duration(boot_ts - prev['last_ts'])}")
        if not clean and net_uptime and net_uptime >= 60:
            lines.append(f"Інтернет з'явився через {fmt_duration(net_uptime)} після завантаження")
    else:
        lines.append("⏱ час не синхронізований, тривалість невідома")

    if prev["undervolt"] and not clean:
        lines.append("⚠️ перед цим фіксувались просідання напруги (Under-voltage)")

    return "\n".join(lines)


def send_telegram(text):
    data = urllib.parse.urlencode({"chat_id": CHAT_ID, "text": text}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", data=data
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError) as e:
        log(f"Telegram error: {e}")
        return False


def main():
    if not BOT_TOKEN or not CHAT_ID:
        log("BOT_TOKEN / BOT_CHAT_ID not set")
        return 1

    deadline = time.monotonic() + MAX_WAIT_SEC
    net_since = None
    net_uptime = None
    synced = False

    # Чекаємо на інтернет; після появи ще до SYNC_GRACE_SEC на синхронізацію часу
    while True:
        if have_internet():
            if net_since is None:
                net_since = time.monotonic()
                net_uptime = uptime_sec()  # скільки після буту піднявся інтернет
            synced = time_synced()
            if synced or time.monotonic() - net_since > SYNC_GRACE_SEC:
                break
        if time.monotonic() > deadline:
            log("Gave up waiting for internet")
            return 1
        time.sleep(RETRY_SEC)

    text = build_message(previous_boot(), synced, net_uptime)
    log(text)

    while not send_telegram(text):
        if time.monotonic() > deadline:
            log("Gave up sending to Telegram")
            return 1
        time.sleep(RETRY_SEC)

    return 0


if __name__ == "__main__":
    sys.exit(main())