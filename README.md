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
│   │   │   ├── outage_notify/  # Telegram alert after boot (power outage / reboot), all Pis
│   │   │   └── thermal_cam/    # Pi Zero thermal camera: scripts, systemd units, secrets
│   │   │       ├── files/      # thermal_web.py, supabase_sync.py, systemd units
│   │   │       └── README.md   # thermal camera dashboard docs
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

Manual run of a single Pi from GitHub: Actions → `Run Ansible Playbook` → Run workflow → `limit` (`rpi-pi4`, `rpi-zero`, `rpi-pi5`).

## Features

### Monitoring & Alerts

- **Thermal Camera Dashboard**: MLX90640 thermal camera web stream, BME280 telemetry, power outage tracking and daily Supabase sync. Deployed by the `thermal_cam` role, see [ansible/raspberry/roles/thermal_cam/README.md](ansible/raspberry/roles/thermal_cam/README.md)
- **System Monitoring (Beszel)**: lightweight system metrics via `beszel_agent` role and Docker container
- **Power Outage Monitoring**: scheduled power outages with Telegram alerts, see [scripts/light_outage/README.MD](scripts/light_outage/README.MD)
- **Power Loss / Reboot Alerts**: `outage_notify` role on every Pi. After boot a systemd unit waits for internet and sends a Telegram message: power loss (with duration, undervoltage hint), kernel panic, ping-reboot or clean reboot. A per-minute heartbeat timer gives the outage moment with ~1 min precision (`/var/lib/raspberry-outage/heartbeat`)
- **Telegram Notifications**: automatic failure notifications for all workflows

### Infrastructure Management

- **Ansible Playbooks**: server configuration and Docker app deployment
- **Docker Apps Backup & Restore**: weekly archive of `/home/pi/docker` into the shared Syncthing folder, restore on a fresh SD card via Ansible, see [Docker Apps Backup & Restore](#docker-apps-backup--restore)
- **Watchdog Setup**: hardware watchdog for automatic reboot on hangs
- **Cron Management**: cron job setup for scheduled tasks

### Deprecated

Goodreads, IMDb, Feedly and GitHub quotas scripts no longer work (sites removed the feeds / APIs or block scraping). They live in [scripts/deprecated](scripts/deprecated/README.md) together with the last good backups, their workflows are disabled. Use manual CSV export for Goodreads and IMDb.

## Docker Apps Backup & Restore

Goal: if an SD card dies, a new one is brought back to the same state by Ansible. Implemented in the `docker_apps` role (`ansible/raspberry/roles/docker_apps`) for the Pis that run it (pi4, pi5; the Pi Zero thermal cam is not covered, its data goes to Supabase).

### How the backup works

- `/usr/local/bin/docker-backup.sh` (template `docker-backup.sh.j2`) is deployed by Ansible, state lives in `/var/lib/docker-backup` (`last_ok`, `stopped`).
- Cron checks **daily at 03:30** and **once after every boot** (`--if-due`). A backup runs only if the last successful one is at least `docker_backup_due_days` (6) days old, so it is effectively weekly. A run missed because the Pi was off, or interrupted by a power loss, is retried the next day.
- The script stops all running containers (consistent SQLite files), makes `docker_apps_YYYYmmdd_HHMMSS.tar.gz` of `/home/pi/docker` in `/var/tmp`, starts the containers again, validates and `sync`s the archive, then moves it atomically into the Syncthing folder:
  `/home/pi/docker/syncthing/sync/backup_docker/<hostname>/`
- The newest `docker_backup_keep` (3) archives are kept, older ones are removed only after the new one is in place.
- Excluded: `syncthing/sync` (holds the archives), `duplicati/backups`, `portainer/admin_password` (generated by Ansible). The Syncthing config (including the device ID) stays in the archive.
- Containers with `restart_policy: unless-stopped` do not start after a reboot if they were stopped by `docker stop`. If power is lost during a backup, the next start of the script (the boot cron) brings them back first and sends a Telegram warning.
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

## Deployment

Deployment is configured via `tailscale/github-action`, giving CI access to the Tailscale network. Requires:

- **OAuth Credentials**: `TS_OAUTH_CLIENT_ID` and `TS_OAUTH_SECRET` (tag `tag:ci`)

Secrets for the Pis (Telegram, Supabase, proxy keys) are passed to Ansible as environment variables from GitHub Secrets and written to `600` env files on the hosts, never stored in git.

### Required GitHub Secrets

See [.github/workflows/README.md](.github/workflows/README.md) for the complete list.

## Getting Started

1. Clone the repository
2. Configure GitHub Secrets in repository settings
3. Push changes to trigger deployment workflows
4. Scheduled workflows run automatically
