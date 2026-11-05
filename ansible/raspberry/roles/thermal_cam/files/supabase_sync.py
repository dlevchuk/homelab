#!/usr/bin/env python3
"""Daily incremental sync: local SQLite -> Supabase (PostgREST upsert)."""
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request

DB_PATH = os.environ.get(
    "THERMAL_DB_PATH", os.path.expanduser("~/thermal_recordings/thermal.db")
)
URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
KEY = os.environ.get("SUPABASE_KEY", "")
BATCH = 500
MAX_ROWS_PER_RUN = 20000  # запобіжник
OUTAGE_DAYS_BACK = 3      # рядок за сьогодні міг змінитись, тож перешлемо останні дні


def upsert(table, rows, on_conflict):
    if not rows:
        return
    req = urllib.request.Request(
        f"{URL}/rest/v1/{table}?on_conflict={on_conflict}",
        data=json.dumps(rows).encode(),
        method="POST",
        headers={
            "apikey": KEY,
            "Authorization": f"Bearer {KEY}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=minimal",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{table}: HTTP {e.code} {e.read().decode(errors='ignore')}")


def main():
    if not URL or not KEY:
        print("SUPABASE_URL / SUPABASE_KEY not set")
        return 1

    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sync_state (name TEXT PRIMARY KEY, value INTEGER NOT NULL)"
    )
    conn.commit()

    row = conn.execute("SELECT value FROM sync_state WHERE name='bme280_last_ts'").fetchone()
    last_ts = row["value"] if row else 0

    sent = 0
    while sent < MAX_ROWS_PER_RUN:
        rows = conn.execute(
            "SELECT ts, temperature, humidity, pressure FROM bme280_readings "
            "WHERE ts > ? ORDER BY ts ASC LIMIT ?",
            (last_ts, BATCH),
        ).fetchall()
        if not rows:
            break
        upsert("bme280_readings", [dict(r) for r in rows], "ts")
        last_ts = rows[-1]["ts"]
        conn.execute(
            "INSERT INTO sync_state (name, value) VALUES ('bme280_last_ts', ?) "
            "ON CONFLICT(name) DO UPDATE SET value=excluded.value",
            (last_ts,),
        )
        conn.commit()
        sent += len(rows)
    print(f"bme280: synced {sent} rows (last_ts={last_ts})")

    outage = conn.execute(
        "SELECT date, location, outage_minutes, power_minutes, periods_count, fetched_ts "
        "FROM outage_daily WHERE date >= date('now', ?)",
        (f"-{OUTAGE_DAYS_BACK} day",),
    ).fetchall()
    upsert("outage_daily", [dict(r) for r in outage], "date,location")
    print(f"outage_daily: synced {len(outage)} rows")

    conn.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"[sync] ERROR: {e}")
        sys.exit(1)