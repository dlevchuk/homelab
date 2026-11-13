# raspberry

Home infrastructure repository: Raspberry Pi management (monitoring, deployment via GitHub Actions and Ansible) plus Ansible configs for other personal machines.

## Workflow Status

![workflow](https://github.com/dlevchuk/raspberry/actions/workflows/telegram_notify.yml/badge.svg)
![workflow](https://github.com/dlevchuk/raspberry/actions/workflows/power.yml/badge.svg)
![workflow](https://github.com/dlevchuk/raspberry/actions/workflows/ansible.yml/badge.svg)

<!-- Disabled / deprecated workflows (kept in .github/workflows with `if: false`) -->
<!-- ![workflow](https://github.com/dlevchuk/raspberry/actions/workflows/imdb_backup.yml/badge.svg) -->
<!-- ![workflow](https://github.com/dlevchuk/raspberry/actions/workflows/goodreads_backup.yml/badge.svg) -->
<!-- ![workflow](https://github.com/dlevchuk/raspberry/actions/workflows/github_quotas.yml/badge.svg) -->

## Overview

- **Monitoring**: Power outage alerts, thermal camera stream, and internet connectivity monitoring
- **Infrastructure as Code**: Ansible playbooks per host (Raspberry Pi, Linux laptop, WSL, Windows)
- **CI/CD**: GitHub Actions workflows for automated deployment and scheduled tasks
- **Docker Applications**: Automated deployment of containerized services on the Pi, with weekly backup and Ansible restore

## Project Structure

```
raspberry/
├── ansible/
│   ├── raspberry/              # Raspberry Pi fleet (deployed by CI)
│   │   ├── roles/
│   │   │   ├── beszel_agent/   # Beszel agent monitoring client
│   │   │   ├── common/         # Common system setup (watchdog, kernel panic config, etc.)
│   │   │   ├── docker_apps/    # Docker application deployments, backup and restore
│   │   │   ├── outage_notify/  # Durable Telegram reboot and internet outage monitor, all Pis
│   │   │   ├── space_cowboy/   # Animated logout banner
│   │   │   ├── thermal_cam/    # Pi Zero thermal camera: scripts, systemd units, secrets
│   │   │   │   ├── files/      # thermal_web.py, supabase_sync.py, systemd units
│   │   │   │   └── README.md   # thermal camera dashboard docs
│   │   │   └── wifi/           # Multi-network Wi-Fi and fallback AP hotspot monitor
│   │   ├── group_vars/
│   │   ├── hosts
│   │   └── site.yml
│   ├── linux_laptop/           # Linux laptop (manual run, TLP battery thresholds)
│   ├── windows/                # Windows host (win10 -> win11 prep, Chocolatey apps, AutoHotkey; manual run, lint in CI)
│   └── wsl/                    # WSL (manual run)
├── scripts/                    # Python scripts for various tasks
│   ├── deprecated/             # Broken / disabled scripts, kept for reference
│   ├── light_outage/
│   └── meteo_data/
└── .github/workflows/
```

## Ansible

Only `ansible/raspberry/**` is deployed by CI (`ansible.yml`, triggered on pushes that touch `ansible/raspberry/**`). Other hosts are run manually:

```bash
ansible-playbook -i ansible/linux_laptop/hosts ansible/linux_laptop/site.yml
ansible-playbook -i ansible/wsl/hosts ansible/wsl/site.yml
# windows: run from its own directory (ansible.cfg), see ansible/windows/README.md
cd ansible/windows && ansible-playbook site.yml
```

Manual run of a single Pi from GitHub: Actions → `Run Ansible Playbook` → Run workflow → `limit` (`rpi-pi4`, `rpi-zero`, `rpi-pi5`). Tick `check` for a dry-run (`--check --diff`, nothing is changed on the hosts). Shell/command tasks are skipped in check mode, so treat the result as a preview, not a guarantee.

## Features

### Monitoring & Alerts

- **Thermal Camera Dashboard**: MLX90640 thermal camera web stream, BME280 telemetry, power outage tracking and daily Supabase sync. Deployed by the `thermal_cam` role, see [ansible/raspberry/roles/thermal_cam/README.md](ansible/raspberry/roles/thermal_cam/README.md)
- **System Monitoring (Beszel)**: lightweight system metrics via `beszel_agent` role and Docker container
- **Power Outage Monitoring**: scheduled power outages with Telegram alerts, see [scripts/light_outage/README.MD](scripts/light_outage/README.MD)
- **Power Loss / Reboot / Internet Alerts**: `outage_notify` runs continuously on every Pi, checking connectivity at roughly 10-second intervals plus probe and delivery time. It reports boot history and internet recovery, including outages without a reboot. Durations below a minute use seconds; NTP waiting and Telegram delivery delays do not inflate the measured outage. A per-minute heartbeat records last activity, which is not proof of the exact power-loss time. See [monitor behavior and limitations](ansible/raspberry/roles/outage_notify/README.md).
- **Telegram Notifications**: automatic failure notifications for all workflows

### Infrastructure Management

- **Ansible Playbooks**: server configuration and Docker app deployment
- **Wi-Fi Multi-Network & Fallback Hotspot**: `wifi` role configures client Wi-Fi networks and an automated emergency hotspot (`<hostname>-ap`). If neither Ethernet nor known Wi-Fi networks are connected after boot (with a 3-minute grace period to let routers boot up after power outages), the Pi activates a fallback AP on `10.42.0.1` (`ipv4.method shared`). A per-minute systemd timer (`wifi-fallback.timer`) periodically retries client networks every 5 minutes if no devices are connected to the hotspot. Off by default, see [Wi-Fi Configuration](#wi-fi-configuration)
- **Docker Apps Backup & Restore**: weekly archive of `/home/pi/docker` into the shared Syncthing folder, restore on a fresh SD card via Ansible, see [Docker Apps Backup & Restore](#docker-apps-backup--restore)
- **Watchdog Setup**: hardware watchdog for automatic reboot on hangs
- **Cron Management**: cron job setup for scheduled tasks

### Deprecated

Goodreads, IMDb, Feedly and GitHub quotas scripts no longer work (sites removed the feeds / APIs or block scraping). They live in [scripts/deprecated](scripts/deprecated/README.md) together with the last good backups, their workflows are disabled. Use manual CSV export for Goodreads and IMDb.

Setting a Docker application’s `enable_*` flag to `false` removes its role-managed container on the next deployment; bind-mounted data and volumes are retained. Unmanaged containers are left alone. The Beszel agent retains its existing rule: it is enabled when either `enable_beszel_agent` or `enable_beszel` is true.

## Docker Apps Backup & Restore

Goal: if an SD card dies, a new one is brought back to the same state by Ansible. Implemented in the `docker_apps` role (`ansible/raspberry/roles/docker_apps`) for the Pis that run it (pi4, pi5; the Pi Zero thermal cam is not covered, its data goes to Supabase).

### How the backup works

- `/usr/local/bin/docker-backup.sh` (template `docker-backup.sh.j2`) is deployed by Ansible, state lives in `/var/lib/docker-backup` (`last_ok`, `stopped`).
- Cron checks **daily at 03:30** and **once after every boot** (`--if-due`). A backup runs only if the last successful one is at least `docker_backup_due_days` (6) days old, so backups normally run every 6 days. A run missed because the Pi was off, or interrupted by a power loss, is retried the next day.
- The script stops all running containers (consistent SQLite files), makes `docker_apps_YYYYmmdd_HHMMSS.tar.gz` of `/home/pi/docker` in `/var/tmp`, starts the containers again, validates and `sync`s the archive, then moves it into the Syncthing folder (atomic only if both directories are on the same filesystem):
  `/home/pi/docker/syncthing/sync/backup_docker/<hostname>/`
- The newest `docker_backup_keep` (3) archives are kept, older ones are removed only after the new one is in place.
- Excluded: `syncthing/sync` (holds the archives), `duplicati/backups`, `portainer/admin_password` (generated by Ansible). The Syncthing config (including the device ID) stays in the archive.
- Containers with `restart_policy: unless-stopped` do not start after a reboot if they were stopped by `docker stop`. If power is lost during a backup, the next start of the script (the boot cron) brings them back first and sends a Telegram warning. If a container cannot be restarted, the recovery list is retained for the next attempt.
- On failure a Telegram message is sent (`TELEGRAM_TOKEN` / `TELEGRAM_TO` from GitHub Secrets, written to `/etc/default/docker-backup`, mode 600). Optional `docker_backup_ping_url` pings an Uptime Kuma push monitor on success.
- The archive is **not encrypted**. Offsite copy is whatever syncs the Syncthing folder to other devices; nothing is stored on a remote service by the Pi itself.

Run now and check:

```bash
sudo /usr/local/bin/docker-backup.sh --if-due   # runs if due (first run: always)
journalctl -t docker-backup
ls -la /home/pi/docker/syncthing/sync/backup_docker/$(hostname)/
```

Main variables (`ansible/raspberry/roles/docker_apps/defaults/main.yml`): `enable_docker_backup`, `docker_backup_hour`, `docker_backup_minute`, `docker_backup_due_days`, `docker_backup_keep`, `docker_backup_dest`, `docker_backup_stop_containers`, `docker_backup_excludes`.

### Restore on a fresh SD card

1. Flash the card, create user `pi`, enable SSH and join Tailscale (this bootstrap is **not** done by Ansible).
2. Copy the latest archive from a device that has the Syncthing folder to the Pi:
   ```bash
   ssh pi@<host> 'mkdir -p /var/tmp/docker-restore'
   scp docker_apps_<timestamp>.tar.gz pi@<host>:/var/tmp/docker-restore/
   ```
3. Run Actions → `Run Ansible Playbook` → `limit` for that Pi (or push a change under `ansible/raspberry/**`).
4. The `docker_restore.yml` tasks run **before any container is created**. If `/home/pi/docker` does not exist, the newest `docker_apps_*.tar.gz` from `docker_restore_dir` (`/var/tmp/docker-restore`) is extracted with owners and permissions preserved, then the normal deployment starts the containers on the restored data. If no archive is found, Ansible only prints a warning and deploys an empty stack.
5. Restore over an existing installation: `-e docker_restore_force=true` (running containers are stopped first).

After a restore the Syncthing folder `sync` is empty and has no `.stfolder` marker, so Syncthing reports a folder error. This is a safeguard against propagating "deletions" to other devices. The safest fix is to remove the folder in the Syncthing UI and add it again.

Do not leave a freshly deployed empty stack running unattended for weeks: the next weekly backups would push the good archives out of the 3 kept ones.

## Wi-Fi Configuration

The `wifi` role gives a Pi several client Wi-Fi networks plus a fallback hotspot (behaviour is described in [Features](#infrastructure-management)). It is **off by default**: the role runs only where `enable_wifi: true` is set in `ansible/raspberry/group_vars/<group>.yml` (enabled for pi4 and pi5). Both use NetworkManager and the same `WIFI_CONFIG` format. NetworkManager is the only supported backend.

The role does not migrate a Pi from dhcpcd / wpa_supplicant to NetworkManager. That is done by hand once, before the first run of the role. On the NetworkManager backend the role installs `dnsmasq-base`, `python3-gi` and `gir1.2-nm-1.0` (needed by the `nmcli` Ansible module).

Pi 5 with Raspberry Pi OS Trixie already uses NetworkManager. Existing netplan-generated NetworkManager connections (such as `netplan-wlan0-<ssid>`) are preserved; the role adds its `wifi-<ssid>` profiles without restarting NetworkManager or applying netplan. The global `wpa_supplicant` service can remain active because NetworkManager uses it for Wi-Fi authentication.

### `WIFI_CONFIG` secret

Networks and the hotspot password are not stored in git. They come from the GitHub Secret `WIFI_CONFIG`, a single JSON object that CI exports as an environment variable:

```json
{
  "ap_password": "<min-8-chars>",
  "networks": [
    { "ssid": "Home-5G",        "psk": "<wifi-password>" },
    { "ssid": "Backup-Hotspot", "psk": "<wifi-password>" },
    { "ssid": "Hidden-Network", "psk": "<wifi-password>", "hidden": true },
    { "ssid": "Open-Network" }
  ]
}
```

| Field | Type | Required | Description |
| :--- | :--- | :---: | :--- |
| `ap_password` | string | no | WPA2 password of the fallback hotspot, 8-63 characters. If empty or missing, the hotspot profile is not created |
| `networks` | array | no | Client networks. If empty or missing, no client profiles are created |
| `networks[].ssid` | string | yes | Network name. Always a JSON **string**, so numeric names must be quoted (`"49"`, not `49`) |
| `networks[].psk` | string | no | WPA2 password. If empty or missing, the network is configured as open |
| `networks[].hidden` | boolean | no | `true` for a hidden SSID, default `false` |

Rules and behaviour:

- It must be valid JSON: no trailing commas, lowercase `true` / `false`, double quotes only, and `"` or `\` inside a password must be escaped. Validate before pasting into the secret: `jq -c . wifi.json` prints a compact one-line JSON or the error (do not commit the file).
- All networks have the same priority (`wifi_client_priority`, 10): the Pi connects to whichever known network is available.
- Profiles are named `wifi-<ssid>` and are created or updated by Ansible. Connections that were not created by the role are left alone.
- The hotspot SSID is `<inventory_hostname>-ap` (for example `rpi-pi4-ap`), its address is `10.42.0.1/24`, and a connected client reaches the Pi with `ssh pi@10.42.0.1`.
- Legacy fallback: if `WIFI_CONFIG` is empty, the role reads the separate secrets `WIFI_NETWORKS` (JSON array of the same network objects) and `WIFI_AP_PASSWORD`.
- If no Wi-Fi secrets are set, the role skips client and hotspot profile creation, but still installs dependencies and deploys the fallback timer and script. Existing profiles are preserved.

To change networks: update the secret, then run Actions → `Run Ansible Playbook` with `limit` for that Pi (first with `check`, if you want to see the diff).

## Deployment

Deployment is configured via `tailscale/github-action`, giving CI access to the Tailscale network. Requires:

- **OAuth Credentials**: `TS_OAUTH_CLIENT_ID` and `TS_OAUTH_SECRET` (tag `tag:ci`)

Secrets for the Pis (Telegram, Supabase, proxy keys) are passed to Ansible as environment variables from GitHub Secrets and written to `600` env files on the hosts, never stored in git.

### Required GitHub Secrets

- **Tailscale**: `TS_OAUTH_CLIENT_ID`, `TS_OAUTH_SECRET` (tag `tag:ci`)
- **SSH & Fleet**: `SSH_PRIVATE_KEY`, `REMOTE_USER`, `REMOTE_HOST`, `REMOTE_PORT`, `THERMAL_REMOTE_HOST`, `THERMAL_REMOTE_PORT`, `PI5_REMOTE_HOST`, `PI5_REMOTE_PORT`
- **Wi-Fi Configuration**: `WIFI_CONFIG` (JSON with client networks and the hotspot password, format in [Wi-Fi Configuration](#wi-fi-configuration))
- **Notifications & Cloud**: `TELEGRAM_TOKEN`, `TELEGRAM_TO`, `SUPABASE_URL`, `SUPABASE_KEY`, `SCRAPER_API_KEY`, `ZENROWS_API_KEY`

## Getting Started

1. Clone the repository
2. Configure GitHub Secrets in repository settings
3. Push changes to trigger deployment workflows
4. Scheduled workflows run automatically

## Local checks

Run offline regression tests with `python3 -m unittest discover -s tests -v`. See [repository review](docs/code-review-2026-11-12.md) for fixes, validation coverage and remaining issues.

## Log retention

The `logging` role applies to all Raspberry Pis, including Pi Zero: system journals
use a 100 MiB target and seven-day retention; rsyslog text logs rotate daily with
seven compressed archives and a 10 MiB threshold. Ansible-managed Docker containers
use three 10 MiB log files each. The first full deployment may recreate containers
to apply these limits. See [policy, overrides and verification](ansible/raspberry/roles/logging/README.md).
