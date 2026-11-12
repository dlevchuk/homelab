# Deprecated scripts

Scripts that no longer work. Kept for reference only, their workflows are disabled.

| Script | Status | Why | Replacement |
| :--- | :--- | :--- | :--- |
| `backup_goodreads_web/` | broken | Goodreads retired shelf RSS feeds in Oct 2026 (`/review/list_rss/` returns 404), shelf pages require sign-in | Manual CSV: Goodreads → My Books → Import and export → Export Library |
| `backup_imdb_web/` | broken | IMDb blocks the scraper, lists page cannot be fetched | Manual CSV export from IMDb (ratings, watchlist, lists) |
| `backup_imdb_outdated/` | outdated | Cookie-based export no longer works after IMDb redesign | see above |
| `backup_feedly/` | disabled | Feedly developer tokens require Pro since Oct 2023 | none |
| `github_quotas/` | deprecated | GitHub billing API endpoints removed | none |

Historical backups stay in their directories. `backup_imdb_web/imdb_backup.json` contains an older scraper log followed by JSON, so it is not directly parseable as JSON; preserve the original when recovering its data.

Workflows are kept in `.github/workflows/` with `if: false` (manual `workflow_dispatch` only, job is skipped).
Paths inside them already point to `scripts/deprecated/...`, so they can be re-enabled by removing `if: false`.
