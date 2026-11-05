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
- **Infrastructure as Code**: Ansible playbooks per host (Raspberry Pi, Linux laptop, WSL)
- **CI/CD**: GitHub Actions workflows for automated deployment and scheduled tasks
- **Docker Applications**: Automated deployment of containerized services on the Pi

## Project Structure

```
raspberry/
├── ansible/
│   ├── raspberry/              # Raspberry Pi fleet (deployed by CI)
│   │   ├── roles/
│   │   │   ├── beszel_agent/   # Beszel agent monitoring client
│   │   │   ├── common/         # Common system setup (watchdog, kernel panic config, etc.)
│   │   │   ├── docker_apps/    # Docker application deployments
│   │   │   ├── outage_notify/  # Telegram alert after boot (power outage / reboot), all Pis
│   │   │   └── thermal_cam/    # Pi Zero thermal camera: scripts, systemd units, secrets
│   │   │       ├── files/      # thermal_web.py, supabase_sync.py, systemd units
│   │   │       └── README.md   # thermal camera dashboard docs
│   │   ├── group_vars/
│   │   ├── hosts
│   │   └── site.yml
│   ├── linux_laptop/           # Linux laptop (manual run, TLP battery thresholds)
│   └── wsl/                    # WSL (manual run)
├── scripts/                    # Python scripts for various tasks
│   ├── deprecated/             # Broken / disabled scripts, kept for reference
│   ├── light_outage/
│   └── meteo_data/
└── .github/workflows/
```

## Ansible

Only `ansible/raspberry/**` is deployed by CI (`ansible.yml`). Other hosts are run manually:

```bash
ansible-playbook -i ansible/linux_laptop/hosts ansible/linux_laptop/site.yml
ansible-playbook -i ansible/wsl/hosts ansible/wsl/site.yml
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
- **Watchdog Setup**: hardware watchdog for automatic reboot on hangs
- **Cron Management**: cron job setup for scheduled tasks

### Deprecated

Goodreads, IMDb, Feedly and GitHub quotas scripts no longer work (sites removed the feeds / APIs or block scraping). They live in [scripts/deprecated](scripts/deprecated/README.md) together with the last good backups, their workflows are disabled. Use manual CSV export for Goodreads and IMDb.

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