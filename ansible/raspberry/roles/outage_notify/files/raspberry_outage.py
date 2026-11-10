#!/usr/bin/env python3
"""
Після завантаження Pi чекає на інтернет і шле в Telegram, чому він перезавантажився:
- kernel panic / lockup
- ping-reboot (немає мережі)
- штатне перезавантаження
- нештатне вимкнення (ймовірно світло), з тривалістю і ознаками просідання напруги

Момент втрати живлення = max(heartbeat, останній запис журналу) "справжнього" попереднього буту.
"Флап" = короткий (< MIN_BOOT_SEC) бут, що закінчився нештатно (світло блимало після відновлення):
такі бути пропускаються, аналізується перший нормальний бут перед ними.
Тривалість буту береться з монотонного часу журналу (не залежить від фейкового годинника без RTC).

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
MIN_BOOT_SEC = 10 * 60      # бут коротший за це і нештатно завершений = флап
MAX_BOOTS_BACK = 10         # скільки бутів назад дивимось максимум

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


def norm_id(s):
    """boot_id у /proc має дефіси, у журналі (_BOOT_ID) ні."""
    return (s or "").replace("-", "").lower()


def read_heartbeat(bid):
    """Heartbeat конкретного буту: шукаємо boot_id в heartbeat і heartbeat.prev."""
    want = norm_id(bid)
    if not want:
        return None
    base = os.path.join(HEARTBEAT_DIR, "heartbeat")
    for path in (base, base + ".prev"):
        try:
            with open(path) as f:
                b, ts = f.read().split()
            if norm_id(b) == want:
                return float(ts)
        except (OSError, ValueError):
            continue
    return None


def boot_last_entry(offset):
    """Останній запис журналу буту з відносним номером offset (-1, -2, ...) або None."""
    out = journal("-b", str(offset), "-n", "1", "-o", "json").strip()
    if not out:
        return None
    try:
        e = json.loads(out.splitlines()[-1])
        return {
            "bid": e.get("_BOOT_ID", ""),
            "journal_ts": int(e["__REALTIME_TIMESTAMP"]) / 1e6,
            # монотонний час останнього запису ~ тривалість буту, не залежить від годинника
            "uptime": int(e["__MONOTONIC_TIMESTAMP"]) / 1e6,
        }
    except (ValueError, KeyError, IndexError):
        return None


def analyze_boot(offset, info):
    """Збирає ознаки завершення буту offset."""
    hb_ts = read_heartbeat(info["bid"])
    candidates = [t for t in (info["journal_ts"], hb_ts) if t]
    last_ts = max(candidates)
    src = "heartbeat" if hb_ts and hb_ts >= info["journal_ts"] else "журнал"

    tail = journal("-b", str(offset), "-n", "100", "-o", "cat")
    kern = journal("-b", str(offset), "-k", "-n", "5000", "-o", "cat")
    return {
        "last_ts": last_ts,
        "src": src,
        "uptime": info["uptime"],
        "clean": bool(CLEAN_SHUTDOWN_RE.search(tail)),
        "ping_reboot": bool(PING_REBOOT_RE.search(tail)),
        "panic": bool(PANIC_RE.search(kern)),
        "undervolt": bool(UNDERVOLT_RE.search(kern)),
    }


def previous_boot():
    """Аналіз останнього 'справжнього' буту перед поточним (флапи пропускаємо) або None."""
    cur = norm_id(boot_id())
    flaps = 0
    first_seen = None  # fallback, якщо нормального буту не знайшлось

    for n in range(1, MAX_BOOTS_BACK + 1):
        offset = -n
        info = boot_last_entry(offset)
        if info is None:
            break
        if cur and norm_id(info["bid"]) == cur:
            continue  # нас самих рахувати не треба

        res = analyze_boot(offset, info)
        log(f"boot {offset}: id={info['bid'][:8]} uptime={int(info['uptime'])}s "
            f"clean={res['clean']} panic={res['panic']} last_ts={int(res['last_ts'])} ({res['src']})")
        if first_seen is None:
            first_seen = res

        is_flap = res["uptime"] < MIN_BOOT_SEC and not res["clean"] and not res["panic"]
        if is_flap:
            flaps += 1
            continue

        res["flaps"] = flaps
        return res

    if first_seen is None:
        return None
    first_seen["flaps"] = 0  # всі бути були флапами, показуємо найсвіжіший як є
    return first_seen


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

    if prev.get("flaps"):
        lines.append(f"🔁 після відновлення було ще {prev['flaps']} коротких запусків (світло блимало)")

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