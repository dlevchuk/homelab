# light outage

[← Documentation](index.md) · [Source files](../scripts/light_outage/)

The script reads the community-maintained [Chernihivoblenergo schedule JSON](https://github.com/yaroslav2901/OE_OUTAGE_DATA/blob/main/data/Chernihivoblenergo.json) and sends a text summary to Telegram and optionally Discord. It uses the current Kyiv date and includes the next scheduled outage, the time until it, and all intervals for the selected group.

GitHub Actions runs the script daily and supports manual runs. The workflow uses the existing `LOCATION_NAME`, `TELEGRAM_TOKEN`, `TELEGRAM_TO`, and optional `DISCORD_WEBHOOK_URL` secrets. The repository variable `OUTAGE_GROUP` defaults to `5.1` (Tuzhar, Molodizhna 23). Set it to match the configured address if that address changes.

From the repository root, run locally with:

```sh
python -m pip install -r scripts/light_outage/requirements.txt
LOCATION_NAME='Тужар' OUTAGE_GROUP=5.1 python scripts/light_outage/power.py
```

Set `TG_TOKEN` and `TG_CHAT_ID` to enable Telegram delivery, and optionally set `DISCORD_WEBHOOK_URL`. The JSON download is public and requires no proxy credentials. A missing date, missing group, incomplete schedule, or fetch failure is reported as unavailable. A complete schedule with no outage slots is reported as no outages.
