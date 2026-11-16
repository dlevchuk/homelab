# Deployment and CI

[← Repository overview](../README.md)

## Workflows

CI runs once per pull-request update, with conditional Python tests, Windows Ansible lint,
and GitHub Actions security checks. New commits cancel outdated CI runs. The weekly CI
schedule runs only the security audit; a manual CI run executes all checks. File formatting
and secret detection remain in pre-commit.ci rather than being duplicated in Actions.
The stable `CI checks` job can be used as a required check, including for documentation-only PRs.
When migrating branch protection, replace the old `lint` / `zizmor` required checks with
`CI checks`; keep the pre-commit.ci check if already required.

Deployment runs on changes to `ansible/raspberry/**` on `main`, or manually. Power alerts
keep their daily GitHub schedule. Both send Telegram failure notifications within their
own jobs, without starting a separate notification workflow. Deployment is not interrupted
by newer runs. Retired workflows were removed; their history remains in Git.

## Ansible

Only `ansible/raspberry/**` is deployed by CI (`ansible.yml`, triggered on pushes that touch `ansible/raspberry/**`). Other hosts are run manually:

```bash
ansible-playbook -i ansible/linux_laptop/hosts ansible/linux_laptop/site.yml
ansible-playbook -i ansible/wsl/hosts ansible/wsl/site.yml
# windows: run from its own directory (ansible.cfg), see docs/windows.md
cd ansible/windows && ansible-playbook site.yml
```

Manual run of a single Pi from GitHub: Actions → `Run Ansible Playbook` → Run workflow → `limit` (`rpi-pi4`, `rpi-zero`, `rpi-pi5`). Tick `check` for a dry-run (`--check --diff`, nothing is changed on the hosts). Shell/command tasks are skipped in check mode, so treat the result as a preview, not a guarantee.

## Deployment

Deployment is configured via `tailscale/github-action`, giving CI access to the Tailscale network. Requires:

- **OAuth Credentials**: `TS_OAUTH_CLIENT_ID` and `TS_OAUTH_SECRET` (tag `tag:ci`)

Secrets for the Pis (Telegram, Supabase, proxy keys) are passed to Ansible as environment variables from GitHub Secrets and written to `600` env files on the hosts, never stored in git.

### Required GitHub Secrets

- **Tailscale**: `TS_OAUTH_CLIENT_ID`, `TS_OAUTH_SECRET` (tag `tag:ci`)
- **SSH & Fleet**: `SSH_PRIVATE_KEY`, `REMOTE_USER`, `REMOTE_HOST`, `REMOTE_PORT`, `THERMAL_REMOTE_HOST`, `THERMAL_REMOTE_PORT`, `PI5_REMOTE_HOST`, `PI5_REMOTE_PORT`
- **Wi-Fi Configuration**: `WIFI_CONFIG` (JSON with client networks and the hotspot password, format in [Wi-Fi Configuration](wifi.md))
- **Notifications & Cloud**: `TELEGRAM_TOKEN`, `TELEGRAM_TO`, `SUPABASE_URL`, `SUPABASE_KEY`, `SCRAPER_API_KEY`, `ZENROWS_API_KEY`

## Disabling Docker applications

Setting a Docker application’s `enable_*` flag to `false` removes its role-managed container on the next deployment; bind-mounted data and volumes are retained. Unmanaged containers are left alone. The Beszel agent retains its existing rule: it is enabled when either `enable_beszel_agent` or `enable_beszel` is true.

## Local checks

```bash
python3 -m unittest discover -s tests -v
pre-commit run --all-files
```

## Log retention

The `logging` role applies to all Raspberry Pis, including Pi Zero: system journals
use a 100 MiB target and seven-day retention; rsyslog text logs rotate daily with
seven compressed archives and a 10 MiB threshold. Ansible-managed Docker containers
use three 10 MiB log files each. The first full deployment may recreate containers
to apply these limits. See [policy, overrides and verification](logging.md).
