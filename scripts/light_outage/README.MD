# light outage
This script fetches the power outage schedule from energy-ua.info (via ScraperAPI / ZenRows proxies) and sends a daily summary to Telegram (and optionally Discord).

## Installation:

 Requirements

     Python 3.x
     cloudscraper
     beautifulsoup4
     requests
     pytz
     python-dotenv

Install: `pip install -r requirements.txt`


## How it runs:

Normally via GitHub Actions (`.github/workflows/power.yml`, daily at 22:00 UTC) with secrets:
`TELEGRAM_TOKEN`, `TELEGRAM_TO`, `SCRAPER_API_KEY`, `ZENROWS_API_KEY`, `DISCORD_WEBHOOK_URL`, `LOCATION_NAME`, `LOCATION_URL`.

## How to run locally:

`power.py` reads plain environment variables and does **not** load a `.env` file by itself, so export them first:

1. Create .env file:
   ```
     #telegram
     TG_TOKEN=
     TG_CHAT_ID=

     #proxies (at least one is needed, the direct request is usually blocked)
     SCRAPER_API_KEY=
     ZENROWS_API_KEY=

     #discord (optional)
     DISCORD_WEBHOOK_URL=

     #location configuration (required)
     LOCATION_NAME=Name of your location (example: Чернігів)
     LOCATION_URL=https://chernigiv.energy-ua.info/grafik/
   ```

2. Load it and run the script:
   ```
     set -a; source .env; set +a
     python power.py
   ```

## Features:

- Daily schedule: sends a summary of all scheduled outages (next outage, time until it, all periods)
- Fetch strategy: ScraperAPI standard request, then ScraperAPI premium, then ZenRows as a fallback
- Timezone-aware: uses Ukraine timezone (Europe/Kyiv)
- Telegram and optional Discord delivery

> Not implemented: real-time alerts ("30 minutes before an outage" / "power restored"). The script runs once per day.