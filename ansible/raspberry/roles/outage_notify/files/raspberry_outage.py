#!/usr/bin/env python3
"""
Постійно перевіряє мережу, зберігає події на диску та надсилає їх у Telegram.
Після завантаження пояснює можливу причину попереднього завершення:
- kernel panic / lockup
- ping-reboot (немає мережі)
- штатне перезавантаження
- нештатне вимкнення (ймовірно світло), з тривалістю і ознаками просідання напруги

Оцінка останньої активності = max(heartbeat, останній запис журналу) "справжнього" попереднього буту.
"Флап" = короткий (< MIN_BOOT_SEC) бут, що закінчився нештатно (світло блимало після відновлення):
такі бути пропускаються, аналізується перший нормальний бут перед ними.
Тривалість буту береться з монотонного часу журналу (не залежить від фейкового годинника без RTC).

Heartbeat пише raspberry-outage-heartbeat.timer (heartbeat.sh) раз на хвилину.

Запускається systemd-юнітом raspberry-outage.service (ansible/raspberry/roles/outage_notify).
Env: BOT_TOKEN, BOT_CHAT_ID, HOST_LABEL (опційно), HEARTBEAT_DIR (опційно).
"""
import json
import fcntl
import math
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
SYNC_GRACE_SEC = 10 * 60    # скільки чекати NTP при вже наявному інтернеті
MIN_BOOT_SEC = 10 * 60      # бут коротший за це і нештатно завершений = флап
MAX_BOOTS_BACK = 10         # скільки бутів назад дивимось максимум

# Ознаки штатного завершення в кінці журналу попереднього буту
CLEAN_SHUTDOWN_RE = re.compile(
    r"(reboot|poweroff|halt)\.target|Reached target .*(Reboot|Power-Off|Halt)",
    re.IGNORECASE,
)
PING_REBOOT_RE = re.compile(r"Rebooting system")  # повідомлення ping-reboot-check.sh
PANIC_RE = re.compile(r"Kernel panic|watchdog: .*LOCKUP", re.IGNORECASE)
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
        return r.returncode == 0 and r.stdout.strip() == "yes"
    except (OSError, subprocess.SubprocessError):
        return False


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
                value = float(ts)
                if math.isfinite(value) and value > 0:
                    return value
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
    ping_log = journal("-b", str(offset), "-t", "ping-reboot", "-n", "20", "-o", "cat")
    kern = journal("-b", str(offset), "-k", "-n", "5000", "-o", "cat")
    return {
        "last_ts": last_ts,
        "src": src,
        "uptime": info["uptime"],
        "clean": bool(CLEAN_SHUTDOWN_RE.search(tail)),
        "ping_reboot": bool(PING_REBOOT_RE.search(ping_log)),
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
    first_seen["flaps"] = max(0, flaps - 1)
    return first_seen


def fmt_duration(seconds):
    if seconds < 60:
        return f"{max(0, int(seconds))} с"
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


def build_message(prev, synced, net_uptime, *, boot_ts=None, recovery_ts=None,
                  outage_sec=None, outage_detected=False):
    if prev is None:
        lines = [f"ℹ️ {HOST}: завантажився, інтернет є (даних попереднього буту нема)"]
        if net_uptime is not None:
            lines.append(f"Інтернет виявлено через {fmt_duration(net_uptime)} після завантаження")
        if outage_sec is not None:
            lines.append(f"Без інтернету (за перевірками): {fmt_duration(outage_sec)}")
        elif outage_detected:
            lines.append("⏱ тривалість збою мережі невідома: немає надійного часу початку")
        return "\n".join(lines)

    clean = prev["clean"] and not prev["panic"]
    if prev["panic"]:
        head = f"💥 {HOST}: у попередньому запуску зафіксовано kernel panic / lockup"
    elif prev["ping_reboot"]:
        head = f"🌐 {HOST}: перезавантаження через відсутність мережі (ping-reboot)"
    elif clean:
        head = f"🔄 {HOST}: запуск після штатного завершення роботи"
    else:
        head = f"⚡ {HOST}: запуск після нештатного завершення (можлива втрата живлення або зависання)"

    last = datetime.fromtimestamp(prev["last_ts"]).astimezone()
    lines = [
        head,
        f"Остання активність: {last.strftime('%Y-%m-%d %H:%M:%S %Z')} ({prev['src']})",
    ]

    if synced and boot_ts is not None:
        off_sec = boot_ts - prev["last_ts"]
        if off_sec >= 0:
            lines.append(f"Від останньої активності до поточного запуску: {fmt_duration(off_sec)}")
        else:
            lines.append("⏱ час попереднього журналу некоректний, проміжок між запусками невідомий")
    else:
        lines.append("⏱ немає надійного часу запуску, проміжок між запусками невідомий")

    if outage_sec is not None:
        lines.append(f"Без інтернету (за перевірками): {fmt_duration(outage_sec)}")
    elif outage_detected:
        lines.append("⏱ тривалість збою мережі невідома: немає надійного часу початку")
    elif synced and recovery_ts is not None and recovery_ts >= prev["last_ts"]:
        lines.append(f"Від останньої активності до виявлення інтернету: {fmt_duration(recovery_ts - prev['last_ts'])}")

    if net_uptime is not None:
        lines.append(f"Інтернет виявлено через {fmt_duration(net_uptime)} після завантаження")

    if prev.get("flaps"):
        lines.append(f"🔁 перед поточним було ще {prev['flaps']} коротких нештатних запусків")

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
            return r.status == 200 and json.loads(r.read()).get("ok") is True
    except (urllib.error.URLError, OSError, ValueError) as e:
        log(f"Telegram error: {type(e).__name__}")
        return False


def save_state(state):
    """Атомарна заміна: подія залишається на диску до підтвердження Telegram."""
    path = os.path.join(HEARTBEAT_DIR, "monitor.json")
    temporary = path + ".tmp"
    with open(temporary, "w") as f:
        json.dump(state, f, ensure_ascii=False, allow_nan=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temporary, path)
    directory = os.open(HEARTBEAT_DIR, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def load_state():
    try:
        with open(os.path.join(HEARTBEAT_DIR, "monitor.json")) as f:
            state = json.load(f)
    except FileNotFoundError:
        return {"boot": None, "online": None, "outage": None, "queue": []}
    # Пошкоджений стан не перезаписуємо: systemd повторить запуск, помилка буде в журналі.
    if not isinstance(state, dict) or not isinstance(state.get("queue"), list):
        raise ValueError("Invalid monitor.json; preserve it for recovery")
    return state


def outage_duration(outage, bid, now, mono):
    if outage is None:
        return None
    if outage["boot"] == bid:
        return max(0, mono - outage["mono"])
    if now is not None and outage.get("ts") is not None:
        seconds = now - outage["ts"]
        if math.isfinite(seconds) and seconds >= 0:
            return seconds
    return None


def record_sample(state, bid, online, synced, now, mono, uptime):
    """Одна перевірка мережі; стан та повідомлення записуються до надсилання."""
    epoch = now if synced else None
    outage = state.get("outage")
    if not online:
        if state.get("recovery") is not None:
            finish_recovery(state, bid, synced, now, mono, uptime)
        if outage is None:
            state["outage"] = {"boot": bid, "mono": mono, "ts": epoch}
        elif outage["boot"] == bid and outage.get("ts") is None and synced:
            outage["ts"] = now - (mono - outage["mono"])
        state["online"] = False
        return

    # Фіксуємо перше відновлення, не додаючи очікування NTP до часу без мережі.
    recovery = state.get("recovery")
    if recovery is None and outage is None and not any("text" not in e for e in state["queue"]):
        state["online"] = True
        return
    if recovery is None:
        recovery = state["recovery"] = {
            "boot": bid, "mono": mono, "uptime": uptime, "ts": epoch,
            "duration": outage_duration(outage, bid, epoch, mono),
            "had_outage": outage is not None,
            "start_ts": outage.get("ts") if outage is not None else None,
        }
        state["outage"] = None
    if recovery["boot"] != bid:
        # Не використовуємо монотонний годинник попереднього запуску.
        state["recovery"] = None
        return record_sample(state, bid, online, synced, now, mono, uptime)
    if recovery["ts"] is None and synced:
        recovery["ts"] = now - (mono - recovery["mono"])
    state["online"] = True
    if not synced and mono - recovery["mono"] < SYNC_GRACE_SEC:
        return

    finish_recovery(state, bid, synced, now, mono, uptime)


def finish_recovery(state, bid, synced, now, mono, uptime):
    recovery = state["recovery"]
    epoch = now if synced else None
    if recovery["ts"] is None and synced:
        recovery["ts"] = now - (mono - recovery["mono"])
    if recovery["duration"] is None and recovery.get("start_ts") is not None and recovery["ts"] is not None:
        duration = recovery["ts"] - recovery["start_ts"]
        if math.isfinite(duration) and duration >= 0:
            recovery["duration"] = duration
    pending = [event for event in state["queue"] if "text" not in event]
    for event in pending:
        same_boot = event["boot"] == bid
        event["text"] = build_message(
            event["prev"], recovery["ts"] is not None,
            recovery["uptime"] if same_boot else None,
            boot_ts=now - uptime if synced and same_boot else None,
            recovery_ts=recovery["ts"], outage_sec=recovery["duration"],
            outage_detected=recovery["had_outage"],
        )
        event["ready_ts"] = epoch
    if recovery["had_outage"] and not pending:
        duration = recovery["duration"]
        text = f"🌐 {HOST}: інтернет відновлено"
        text += (f"\nБез інтернету (за перевірками): {fmt_duration(duration)}"
                 if duration is not None else "\n⏱ тривалість невідома: немає надійного часу початку збою")
        state["queue"].append({"text": text, "ready_ts": epoch})
    state["recovery"] = None


def run_monitor():
    state = load_state()
    bid = boot_id()
    if not bid:
        raise RuntimeError("Cannot read boot_id")
    if state.get("boot") != bid:
        if state.get("recovery") is not None:
            # Відновлення перед reboot вже відбулося; не губимо цю подію.
            finish_recovery(state, state["recovery"]["boot"], False, 0, 0, 0)
        state["queue"].append({"boot": bid, "prev": previous_boot()})
        state["boot"] = bid
        state["recovery"] = None
        save_state(state)

    while True:
        online = have_internet()
        synced = time_synced()
        now, mono, uptime = time.time(), time.monotonic(), uptime_sec()
        before = json.dumps(state, sort_keys=True)
        record_sample(state, bid, online, synced, now, mono, uptime)
        if json.dumps(state, sort_keys=True) != before:
            save_state(state)
        if online:
            # Обмежуємо пакет, щоб довга черга не блокувала моніторинг мережі.
            for _ in range(5):
                if not state["queue"] or "text" not in state["queue"][0]:
                    break
                event = state["queue"][0]
                text = event["text"]
                if synced and event.get("ready_ts") is not None:
                    delay = time.time() - event["ready_ts"]
                    if delay >= 60:
                        text += f"\nЗатримка доставки після підготовки повідомлення: {fmt_duration(delay)}"
                if not send_telegram(text):
                    break
                state["queue"].pop(0)
                save_state(state)
        time.sleep(RETRY_SEC)


def main():
    if not BOT_TOKEN or not CHAT_ID:
        log("BOT_TOKEN / BOT_CHAT_ID not set")
        return 1
    os.makedirs(HEARTBEAT_DIR, exist_ok=True)
    # Ручний запуск не повинен дублювати постійний сервіс.
    with open(os.path.join(HEARTBEAT_DIR, "monitor.lock"), "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log("Monitor already running")
            return 0
        run_monitor()
    return 0


if __name__ == "__main__":
    sys.exit(main())
