# light outage

[← Documentation](index.md) · [Source files](../scripts/light_outage/)

The script reads the community-maintained [Chernihivoblenergo schedule JSON](https://github.com/yaroslav2901/OE_OUTAGE_DATA/blob/main/data/Chernihivoblenergo.json) and sends a text summary to Telegram and optionally Discord. It uses the current Kyiv date and includes the next scheduled outage, the time until it, and all intervals for the selected group.

## GitHub Actions

The [power alerts workflow](../.github/workflows/power.yml) is scheduled daily at 22:00 UTC and can also be run manually from the Actions tab. The script selects the Kyiv calendar date at the actual start time.

| Setting | Where to configure it | Purpose |
| --- | --- | --- |
| `LOCATION_NAME` | Repository secret | Location label in the message |
| `OUTAGE_GROUP` | Repository variable | Chernihivoblenergo subgroup, written with a dot. Defaults to `5.1` when unset. |
| `TELEGRAM_TOKEN`, `TELEGRAM_TO` | Repository secrets | Telegram bot token and destination chat ID |
| `DISCORD_WEBHOOK_URL` | Optional repository secret | Discord destination |

`LOCATION_URL`, `SCRAPER_API_KEY`, and `ZENROWS_API_KEY` are obsolete for this workflow. The [Pi Zero thermal dashboard](thermal-camera.md) has its own outage fetch configuration and still uses the two proxy keys as optional fallbacks.

## Local run

From the repository root, run locally with:

```sh
python -m pip install -r scripts/light_outage/requirements.txt
LOCATION_NAME='<location label>' OUTAGE_GROUP=5.1 python scripts/light_outage/power.py
```

The example prints the summary. To enable delivery, export `TG_TOKEN` and `TG_CHAT_ID` for Telegram or `DISCORD_WEBHOOK_URL` for Discord.

## Schedule interpretation

The script reads 24 hourly entries for the selected subgroup, converts full and partial hours to outage intervals, and merges adjacent slots. A missing date, missing group, incomplete schedule, unknown slot value, or download failure produces «Не вдалося отримати графік». A complete schedule with zero outage slots produces «Відключень немає».
