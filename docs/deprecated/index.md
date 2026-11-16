# Deprecated scripts

[← Documentation](../index.md) · [Source files](../../scripts/deprecated/)

Run local commands from `scripts/deprecated/` in the repository.

Scripts that no longer work. Kept for reference only, their workflows have been removed (available in Git history).

| Script | Status | Why | Replacement |
| :--- | :--- | :--- | :--- |
| `backup_goodreads_web/` | broken | Goodreads retired shelf RSS feeds in Oct 2026 (`/review/list_rss/` returns 404), shelf pages require sign-in | Manual CSV: Goodreads → My Books → Import and export → Export Library |
| `backup_imdb_web/` | broken | IMDb blocks the scraper, lists page cannot be fetched | Manual CSV export from IMDb (ratings, watchlist, lists) |
| `backup_imdb_outdated/` | outdated | Cookie-based export no longer works after IMDb redesign | see above |
| `backup_feedly/` | disabled | Feedly developer tokens require Pro since Oct 2023 | none |
| `github_quotas/` | deprecated | GitHub billing API endpoints removed | none |

Historical backups stay in their directories. `backup_imdb_web/imdb_backup.json` contains an older scraper log followed by JSON, so it is not directly parseable as JSON; preserve the original when recovering its data.

## Historical guides

- [Goodreads](goodreads.md)
- [IMDb scraper](imdb.md)
- [Legacy IMDb cookie export](imdb-legacy.md)
- [Feedly](feedly.md)
- [GitHub quotas](github-quotas.md)

Script and backup paths in the table are relative to `scripts/deprecated/`.
