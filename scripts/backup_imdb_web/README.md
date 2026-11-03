# IMDB Custom Lists Backup

Automated backup script for IMDB custom lists. This script scrapes public custom lists from an IMDB user profile and exports them to JSON format.

## Overview

The `imdb_scraper.py` script discovers and scrapes all public custom lists for a given IMDB user profile without requiring authentication cookies.

> [!NOTE]
> **Ratings & Watchlist**: IMDB's modern web interface applies aggressive bot detection and rate limits on ratings and watchlist pages. For ratings and watchlist, manually export CSV files directly from IMDB:
> 1. Go to your Ratings: `https://www.imdb.com/user/{USER_ID}/ratings`
> 2. Go to your Watchlist: `https://www.imdb.com/user/{USER_ID}/watchlist`
> 3. Click the 3 dots menu (⋮) → **Export** → Save CSV.
>
> `imdb_scraper.py` focuses specifically on custom lists, which can be scraped quickly and reliably.

## Features

- Scrapes all public custom lists for the configured user ID
- Extracts item details:
  - IMDB ID (`imdb_id`)
  - Title URL (`url`)
  - Title name (`title`)
  - Release year (`year`)
  - IMDB rating (`rating`)
- Handles multi-page list pagination automatically
- Parallel scraping with `ThreadPoolExecutor` (max 2 workers)
- Conservative rate limiting (2s delay per request) and retry logic on HTTP 503
- Outputs structured JSON with summary totals (`total_lists`, `total_items`)

## Usage

### Manual Execution

```bash
python3 imdb_scraper.py > imdb_backup.json
```

The script writes structured JSON to `stdout` and status/progress messages to `stderr`.

### Automated Backup

Run on a schedule using cron or GitHub Actions:

```bash
# Example: monthly backup on the 1st of each month at 3:00 AM
0 3 1 * * cd /path/to/backup_imdb_web && python3 imdb_scraper.py > imdb_backup.json
```

## Configuration

To change the target IMDB user, edit `USER_ID` in `imdb_scraper.py`:

```python
USER_ID = "ur48993532"  # Replace with your IMDB user ID
```

Your user ID can be found in your IMDB profile URL: `https://www.imdb.com/user/{USER_ID}/`

## Dependencies

- Python 3.x
- `requests`
- `beautifulsoup4`

Install dependencies:

```bash
pip install requests beautifulsoup4
```

## Output Format

The script produces a JSON object with the following schema:

```json
{
  "user_id": "ur48993532",
  "scraped_at": "2026-11-01 12:00:00 UTC",
  "lists": [
    {
      "list_id": "ls123456789",
      "list_name": "Favorite Movies",
      "list_url": "https://www.imdb.com/list/ls123456789/",
      "item_count": 25,
      "items": [
        {
          "imdb_id": "tt0111161",
          "url": "https://www.imdb.com/title/tt0111161/",
          "title": "The Shawshank Redemption",
          "year": "1994",
          "rating": "9.3"
        }
      ]
    }
  ],
  "total_lists": 1,
  "total_items": 25
}
```

## Files

- `imdb_scraper.py` - Scraper script
- `imdb_backup.json` - Exported custom lists backup
