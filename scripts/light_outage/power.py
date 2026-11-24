import os
import sys

import requests
from html import escape
from datetime import datetime, timedelta
import pytz

LOCATION_NAME = os.environ.get("LOCATION_NAME")
OUTAGE_GROUP = os.environ.get("OUTAGE_GROUP") or "5.1"
SCHEDULE_URL = (
    "https://raw.githubusercontent.com/yaroslav2901/OE_OUTAGE_DATA"
    "/main/data/Chernihivoblenergo.json"
)
ukraine_tz = pytz.timezone("Europe/Kyiv")

BOT_TOKEN = os.environ.get("TG_TOKEN")
CHAT_ID = os.environ.get("TG_CHAT_ID")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")


def send_telegram(msg):
    if not BOT_TOKEN or not CHAT_ID:
        print("⚠️ Missing Telegram credentials")
        return

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10
        )
        response.raise_for_status()
        print(f"✓ Telegram message sent")
    except Exception as e:
        print(f"Telegram error: {e}")

def send_discord(msg):
    if not DISCORD_WEBHOOK_URL:
        print("⚠️ Discord webhook URL not set, skipping Discord notification")
        return

    try:
        # Convert Telegram HTML tags (<b>) to Discord markdown formatting (**)
        discord_msg = msg.replace("<b>", "**").replace("</b>", "**")

        response = requests.post(
            DISCORD_WEBHOOK_URL,
            json={"content": discord_msg},
            timeout=10
        )
        response.raise_for_status()
        print(f"✓ Discord message sent")
    except Exception as e:
        print(f"Discord error: {e}")

def parse_schedule(data, date, group):
    midnight = datetime.combine(date, datetime.min.time())
    day_key = str(int(ukraine_tz.localize(midnight).timestamp()))
    slots = data["fact"]["data"][day_key][f"GPV{group}"]
    if not isinstance(slots, dict) or set(slots) != {str(hour) for hour in range(1, 25)}:
        raise ValueError("Expected a complete 24-hour schedule")

    # The source numbers hours starting at 1 and marks the half hours without power.
    outage_halves = {
        "yes": (False, False),
        "no": (True, True),
        "first": (True, False),
        "second": (False, True),
        "maybe": (True, True),
        "mfirst": (True, False),
        "msecond": (False, True),
    }
    halves = [half for hour in range(1, 25) for half in outage_halves[slots[str(hour)]]]
    outages = []
    start = None
    for index, is_outage in enumerate([*halves, False]):
        if is_outage and start is None:
            start = ukraine_tz.localize(midnight + timedelta(minutes=30 * index))
        elif not is_outage and start is not None:
            end = ukraine_tz.localize(midnight + timedelta(minutes=30 * index))
            outages.append({
                "start_time": start.strftime("%H:%M"),
                "end_time": end.strftime("%H:%M"),
                "start": start,
                "end": end,
            })
            start = None
    return outages


def fetch_outages(date, group):
    response = requests.get(SCHEDULE_URL, timeout=20)
    response.raise_for_status()
    return parse_schedule(response.json(), date, group)


def format_time_delta(minutes):
    if minutes < 60:
        return f"{int(minutes)} хв"
    hours = minutes / 60
    if hours < 24:
        return f"{hours:.1f} год"
    days = hours / 24
    return f"{days:.1f} днів"

def main():
    if not LOCATION_NAME:
        sys.exit("❌ Error: LOCATION_NAME environment variable must be set.")

    now = datetime.now(ukraine_tz)
    print(f"Script run at: {now.strftime('%Y-%m-%d %H:%M %Z')}\n")
    print(f"Fetching: {LOCATION_NAME} (GPV{OUTAGE_GROUP})")
    try:
        outages = fetch_outages(now.date(), OUTAGE_GROUP)
        print(f"✓ Found {len(outages)} outage periods")
    except (requests.RequestException, KeyError, TypeError, ValueError) as exc:
        print(f"❌ Schedule unavailable: {exc}")
        outages = None

    all_outages = {LOCATION_NAME: outages}

    # Build message
    message_parts = []
    message_parts.append(f"📅 <b>Графік відключень</b>")
    message_parts.append(f"🕐 {now.strftime('%d.%m.%Y %H:%M')}\n")

    for location, outages in all_outages.items():
        message_parts.append(f"🏠 <b>{escape(location)}</b>")

        if outages is None:
            message_parts.append("  ⚠️ Не вдалося отримати графік\n")
            continue

        if not outages:
            message_parts.append("  ✅ Відключень немає\n")
            continue

        # Find next outage
        next_outage = None
        for outage in sorted(outages, key=lambda x: x['start']):
            if outage['start'] > now:
                next_outage = outage
                break

        if next_outage:
            delta_min = (next_outage['start'] - now).total_seconds() / 60
            time_str = format_time_delta(delta_min)

            message_parts.append(f"  ⚡ Наступне відключення:")
            message_parts.append(f"     Через <b>{time_str}</b>")
            message_parts.append(f"     {next_outage['start_time']} – {next_outage['end_time']}\n")

        # Show all outages
        message_parts.append("  📋 Всі відключення:")
        for outage in outages:
            status = ""
            if outage['start'] <= now <= outage['end']:
                status = " 🔴 зараз"
            elif outage['start'] < now:
                status = " ✓ пройшло"

            message_parts.append(f"     {outage['start_time']} – {outage['end_time']}{status}")

        message_parts.append("")

    message = "\n".join(message_parts)

    print(message)
    print(f"{'='*60}\n")

    send_telegram(message)
    send_discord(message)

    print("✓ Script completed")



if __name__ == "__main__":
    main()
