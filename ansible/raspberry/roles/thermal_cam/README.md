# Thermal Camera Web Interface (Raspberry Pi Zero)

`thermal_web.py` is a Python-based web server and video streamer designed for V4L2 thermal camera devices (e.g. `/dev/video0`). It uses `ffmpeg` to capture raw video, calculate Y-channel signal statistics (min, avg, max temperature/brightness), apply color palettes, stream MJPEG video via HTTP, record videos, and serve an interactive web dashboard.

Everything here is deployed by the Ansible role `thermal_cam` (`ansible/raspberry/roles/thermal_cam`) via the `ansible.yml` workflow. Source files live in `files/`.

## Features

- **MJPEG Streaming**: Dynamic rate streaming via `/stream` endpoint with adaptive FPS adjustment.
- **Color Palettes**: Supports `gray`, `blackhot`, `ironbow`, and `rainbow` colormaps.
- **Video Recording**: Start/stop recording MJPEG stream to `.avi` video files (`/record/start`, `/record/stop`).
- **Temperature & Health Monitoring**: `/health` JSON endpoint providing frame counters, streaming FPS, temperature/Y-stats, and restart tracking.
- **BME280 Environmental Monitoring**: Temperature, humidity, and pressure are read over SPI0/CE0 and shown above the Host System card.
- **Buzzer Control**: The dashboard's Signal button plays a PWM tone on GPIO18.
- **Built-in Web Dashboard**: Served directly from `/` with interactive controls, snapshots, full-screen mode, air raid alerts map, and weather radar widget.
- **Systemd Integration**: Runs automatically as a background service via `thermal-web.service`.
- **Daily Supabase Sync**: A systemd timer pushes new BME280 readings and outage stats from local SQLite to Supabase once a day (see [Supabase Sync](#supabase-sync)).
- **Automated CI/CD**: Deployed idempotently by Ansible (`thermal_cam` role) through GitHub Actions over Tailscale. The service is restarted only when its script, unit or env file actually changed.

---

## Requirements

- **OS**: Linux (Raspberry Pi OS / Debian)
- **Python**: Python 3.8+
- **System Dependencies**:
  - `ffmpeg` (compiled with `v4l2` support)
  - V4L2 thermal camera device at `/dev/video0`
  - `python3-spidev` for the BME280 SPI reader
  - `python3-gpiozero` and `python3-lgpio` for PWM buzzer control
  - SPI enabled in Raspberry Pi OS

The `thermal_cam` role installs the packages, enables SPI (`dtparam=spi=on`, reboots the Pi if it changed) and adds the user to the `spi,gpio,video` groups. For a manual setup:

```bash
sudo apt update
sudo apt install -y ffmpeg python3-spidev python3-gpiozero python3-lgpio sqlite3
sudo raspi-config nonint do_spi 0
sudo reboot
```

For manual execution, the current user needs access to the hardware groups:

```bash
sudo usermod -aG spi,gpio,video "$USER"
```

Log out and back in after changing group membership. The supplied systemd service already declares these groups.

`supabase_sync.py` uses only the Python standard library, no extra packages needed.

### Raspberry Pi Pinout

The BME280 is used in **SPI mode**. The built-in reader uses `python3-spidev` directly; it does not require Blinka, a Python virtual environment, or any `pip` packages.

| Device | Device pin | Raspberry Pi physical pin | Function |
| :--- | :--- | :---: | :--- |
| BME280 | `VCC` | 17 | 3.3 V |
| BME280 | `GND` | 9 | Ground |
| BME280 | `SCL` | 23 | GPIO11 / SPI0 SCLK |
| BME280 | `SDA` | 19 | GPIO10 / SPI0 MOSI |
| BME280 | `SDO` | 21 | GPIO9 / SPI0 MISO |
| BME280 | `CSB` | 24 | GPIO8 / SPI0 CE0 |
| MH-FMD buzzer | `VCC` | 4 | 5 V |
| MH-FMD buzzer | `GND` | 6 | Ground |
| MH-FMD buzzer | `SIG` / `I/O` | 12 | GPIO18 / PWM0 |

Do not connect BME280 to 5 V. `i2cdetect` cannot see this BME280 configuration because the module is connected through SPI, not I²C. A working sensor identifies itself with SPI chip ID `0x60`.

---

## HTTP Endpoints

| Endpoint | Method | Description |
| :--- | :--- | :--- |
| `/` | `GET` | HTML dashboard interface |
| `/stream` | `GET` | Live MJPEG video stream (`multipart/x-mixed-replace`) |
| `/snapshot` | `GET` | Single JPEG frame snapshot download |
| `/mode?name=<colormap>` | `GET` | Change color palette (`gray`, `blackhot`, `ironbow`, `rainbow`) |
| `/record/start` | `GET` | Start recording video to `THERMAL_REC_DIR` |
| `/record/stop` | `GET` | Stop video recording |
| `/health` | `GET` | JSON status, FPS stats, Y/temp metrics, recording state |
| `/api/db/bme280?hours=N` | `GET` | BME280 readings from SQLite for last N hours (default 24, max 8760) |
| `/api/db/outage?days=N` | `GET` | Daily outage statistics from SQLite for last N days (default 30) |

---

## Configuration (Environment Variables)

| Variable | Default | Description |
| :--- | :--- | :--- |
| `THERMAL_PORT` | `8080` | HTTP server port |
| `THERMAL_COLORMAP` | `gray` | Default color palette (`gray`, `blackhot`, `ironbow`, `rainbow`) |
| `THERMAL_REC_DIR` | `~/thermal_recordings` | Directory path to store video recordings |
| `THERMAL_DROP_FRAMES` | `20` | Initial frames to drop on stream restart |
| `THERMAL_CAPTURE_FPS` | `30` | V4L2 hardware capture framerate |
| `THERMAL_MAX_FPS` | `30` | Maximum streaming framerate |
| `THERMAL_MIN_FPS` | `3` | Minimum adaptive streaming framerate |
| `THERMAL_QUALITY` | `3` | MJPEG encoding quality (1-31, lower is better) |
| `THERMAL_SCALE` | `""` (none) | Optional scaling (e.g. `320:256`) |
| `THERMAL_STALE_SEC` | `3` | Seconds before marking stream as stalled |
| `THERMAL_TEMP_SCALE` | `""` (none) | Linear calibration multiplier for Y->Temperature conversion |
| `THERMAL_TEMP_OFFSET` | `0` | Linear calibration offset for Y->Temperature conversion |
| `THERMAL_DB_PATH` | `~/thermal_recordings/thermal.db` | Path to SQLite database (also used by `supabase_sync.py`) |
| `BME_DB_INTERVAL` | `300` | Interval in seconds between BME280 DB writes (minimum 60) |
| `SCRAPER_API_KEY` | `""` | ScraperAPI key, proxy for the outage schedule fetch (optional) |
| `ZENROWS_API_KEY` | `""` | ZenRows key, fallback proxy for the outage schedule fetch (optional) |

`SCRAPER_API_KEY` and `ZENROWS_API_KEY` are loaded by `thermal-web.service` from `/home/pi/.thermal-web.env` (see [Secrets handling](#secrets-handling)). The direct request is tried first, proxies are used only when it fails.

Variables used only by `supabase_sync.py` (provided via `/home/pi/.supabase.env`, see below):

| Variable | Description |
| :--- | :--- |
| `SUPABASE_URL` | Project URL, e.g. `https://xxxx.supabase.co` |
| `SUPABASE_KEY` | `service_role` key (bypasses RLS, keep secret) |

---

## Running Manually

```bash
python3 thermal_web.py
```

Or with custom settings:

```bash
THERMAL_PORT=8080 THERMAL_COLORMAP=ironbow python3 thermal_web.py
```

---

## Systemd Service (`thermal-web.service`)

The script is managed via systemd at `/etc/systemd/system/thermal-web.service` (deployed by Ansible, source: `files/thermal-web.service`).

### Service Commands:

```bash
# Enable and start service
sudo systemctl enable --now thermal-web.service

# Check status
sudo systemctl status thermal-web.service

# Restart service
sudo systemctl restart thermal-web.service

# View live logs
sudo journalctl -u thermal-web.service -f
```

---

## Supabase Sync

Once a day (23:55 + up to 2 min random delay) a oneshot systemd unit pushes data from the local SQLite DB (`thermal.db`) to Supabase. `thermal_web.py` is not touched: SQLite runs in WAL mode, so the sync can read it concurrently.

### Components

| File | Purpose |
| :--- | :--- |
| `files/supabase_sync.py` | Incremental, idempotent sync (PostgREST upsert, stdlib only) |
| `files/thermal-sync.service` | `Type=oneshot` unit, loads `/home/pi/.supabase.env` |
| `files/thermal-sync.timer` | Daily trigger, `Persistent=true` (catches up after a missed run / power outage) |

### How it works

- **`bme280_readings`**: incremental by `ts`. Last synced timestamp is stored in the local SQLite table `sync_state` (`name='bme280_last_ts'`). Sent in batches of 500, with a safety cap of 20 000 rows per run. The cursor advances only after a successful batch, so a Supabase outage never loses data: the next run catches up.
- **`outage_daily`**: the last 3 days are re-sent every run (today's row can change during the day). Upsert on `(date, location)`.
- Upserts (`resolution=merge-duplicates`) make re-runs safe, no duplicates.
- First run uploads the whole history, later runs only new rows.

### Free tier budget

| Table | Rate | Estimate |
| :--- | :--- | :--- |
| `bme280_readings` | 1 row / 5 min = ~288 rows/day | ~10-15 MB/year |
| `outage_daily` | 1 row / day | negligible |

The free tier limit is 500 MB of database, so there is a large margin. The daily request also keeps the project from being paused for inactivity (free projects pause after 7 days without activity).

### SQL (run once in Supabase → SQL Editor)

```sql
-- BME280 readings (mirror of local SQLite table, ts = unix seconds)
create table if not exists bme280_readings (
  ts          bigint primary key,
  temperature real,
  humidity    real,
  pressure    real
);

-- Daily outage summary (mirror of local SQLite table)
create table if not exists outage_daily (
  date           text   not null,   -- YYYY-MM-DD
  location       text   not null,
  outage_minutes int    not null,
  power_minutes  int    not null,
  periods_count  int    not null,
  fetched_ts     bigint not null,
  primary key (date, location)
);

-- RLS enabled with NO policies: anon/authenticated have no access,
-- service_role bypasses RLS (used by supabase_sync.py)
alter table bme280_readings enable row level security;
alter table outage_daily    enable row level security;
```

Useful queries:

```sql
-- Row counts and latest data
select count(*) as rows, to_timestamp(max(ts)) as last_reading from bme280_readings;
select * from outage_daily order by date desc limit 7;

-- Daily averages
select date_trunc('day', to_timestamp(ts)) as day,
       round(avg(temperature)::numeric, 1) as temp,
       round(avg(humidity)::numeric, 1)    as hum,
       round(avg(pressure)::numeric, 1)    as pres
from bme280_readings
group by 1
order by 1 desc;

-- Table sizes (to watch the 500 MB free tier limit)
select pg_size_pretty(pg_total_relation_size('bme280_readings')) as bme280,
       pg_size_pretty(pg_total_relation_size('outage_daily'))    as outage,
       pg_size_pretty(pg_database_size(current_database()))      as database;
```

Optional retention (only if the DB ever gets close to the limit):

```sql
delete from bme280_readings where ts < extract(epoch from now() - interval '12 months');
```

### Local SQLite schema (for reference)

Created by `thermal_web.py` (`ThermalDB`):

```sql
CREATE TABLE bme280_readings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL, temperature REAL, humidity REAL, pressure REAL
);
CREATE INDEX idx_bme_ts ON bme280_readings(ts);

CREATE TABLE outage_daily (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  date TEXT NOT NULL, location TEXT NOT NULL,
  outage_minutes INTEGER NOT NULL, power_minutes INTEGER NOT NULL,
  periods_count INTEGER NOT NULL, fetched_ts INTEGER NOT NULL,
  UNIQUE(date, location)
);
```

Created by `supabase_sync.py`:

```sql
CREATE TABLE IF NOT EXISTS sync_state (name TEXT PRIMARY KEY, value INTEGER NOT NULL);
```

### Secrets handling

Keys are **not** stored in git or created by hand. The `thermal_cam` role writes two env files (`chmod 600`, owner `pi`, `no_log`) from GitHub Secrets on every Ansible run:

| File | Content | Used by |
| :--- | :--- | :--- |
| `/home/pi/.supabase.env` | `SUPABASE_URL`, `SUPABASE_KEY` | `thermal-sync.service` |
| `/home/pi/.thermal-web.env` | `SCRAPER_API_KEY`, `ZENROWS_API_KEY` | `thermal-web.service` |

`.supabase.env` is written only when both Supabase secrets are non-empty. `.thermal-web.env` is always written (empty values are fine, the proxies are optional). A change of `.thermal-web.env` restarts `thermal-web`.

Key rotation: update the GitHub secret → run the `Run Ansible Playbook` workflow via `workflow_dispatch` (limit `rpi-zero`).

### Operations

```bash
# Run the sync right now and see the result
sudo systemctl start thermal-sync.service
journalctl -u thermal-sync.service -n 20

# Next scheduled run
systemctl list-timers thermal-sync.timer

# Force a full re-sync (safe, upsert): reset the cursor
sqlite3 ~/thermal_recordings/thermal.db "DELETE FROM sync_state WHERE name='bme280_last_ts';"
```

---

## Deployment (Ansible via GitHub Actions)

The role `ansible/raspberry/roles/thermal_cam` is applied to the `raspberry_zero` group by `ansible/raspberry/site.yml`. The `Run Ansible Playbook` workflow (`.github/workflows/ansible.yml`) runs it over Tailscale on every push to `main` that touches `ansible/**`, or manually.

What the role does:

- installs packages, enables SPI (reboot only if changed), adds the user to `spi,gpio,video`
- copies `thermal_web.py` and `supabase_sync.py` to `/home/pi/`
- writes `/home/pi/.thermal-web.env` and `/home/pi/.supabase.env` (600)
- installs `thermal-web.service`, `thermal-sync.service`, `thermal-sync.timer` into `/etc/systemd/system/`
- enables and starts `thermal-web.service` and `thermal-sync.timer`
- restarts `thermal-web` only if the script, unit or env file changed

If the Pi Zero is unreachable, the dynamic inventory skips it with a warning and the change is **not** applied: re-run the workflow when it is back.

### Manual run

From GitHub: Actions → `Run Ansible Playbook` → Run workflow → `limit: rpi-zero`.

Locally:

```bash
ansible-playbook ansible/raspberry/site.yml -i ansible/raspberry/hosts --limit rpi-zero
```

with `SUPABASE_URL`, `SUPABASE_KEY`, `SCRAPER_API_KEY`, `ZENROWS_API_KEY` exported in the environment.

### Required GitHub Secrets:

- `THERMAL_REMOTE_HOST`: Hostname or Tailscale IP (e.g. `zero.tail72e9f.ts.net`)
- `THERMAL_REMOTE_PORT`: SSH port (default: `22`)
- `REMOTE_USER`: SSH username (e.g. `pi`)
- `SSH_PRIVATE_KEY`: Private SSH key for host authentication
- `TS_OAUTH_CLIENT_ID` / `TS_OAUTH_SECRET`: Tailscale OAuth credentials used by `tailscale/github-action` (tag `tag:ci`)
- `SUPABASE_URL`: Supabase project URL
- `SUPABASE_KEY`: Supabase `service_role` key (written to `/home/pi/.supabase.env`)
- `SCRAPER_API_KEY`: ScraperAPI key (written to `/home/pi/.thermal-web.env`, optional)
- `ZENROWS_API_KEY`: ZenRows key (written to `/home/pi/.thermal-web.env`, optional)

The other secrets used by the workflow (Pi4/Pi5 hosts, Telegram) are listed in `.github/workflows/README.md`.