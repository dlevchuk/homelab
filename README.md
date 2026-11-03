# raspberry

Home infrastructure repository: Raspberry Pi management (automated backups, monitoring, deployment via GitHub Actions and Ansible) plus Ansible configs for other personal machines.

## Workflow Status

![workflow](https://github.com/dlevchuk/raspberry/actions/workflows/telegram_notify.yml/badge.svg)
![workflow](https://github.com/dlevchuk/raspberry/actions/workflows/imdb_backup.yml/badge.svg)
![workflow](https://github.com/dlevchuk/raspberry/actions/workflows/raspberry_outage.yml/badge.svg)
![workflow](https://github.com/dlevchuk/raspberry/actions/workflows/goodreads_backup.yml/badge.svg)
![workflow](https://github.com/dlevchuk/raspberry/actions/workflows/power.yml/badge.svg)
![workflow](https://github.com/dlevchuk/raspberry/actions/workflows/ansible.yml/badge.svg)

<!-- Deprecated: GitHub billing API endpoints have been deprecated -->
<!-- ![workflow](https://github.com/dlevchuk/raspberry/actions/workflows/github_quotas.yml/badge.svg) -->

## Overview

- **Automated Backups**: IMDb and Goodreads data backups
- **Monitoring**: Power outage alerts, thermal camera stream, and internet connectivity monitoring
- **Infrastructure as Code**: Ansible playbooks per host (Raspberry Pi, Linux laptop, WSL)
- **CI/CD**: GitHub Actions workflows for automated deployment and scheduled tasks
- **Docker Applications**: Automated deployment of containerized services on the Pi

## Project Structure

```
raspberry/
├── ansible/
│   ├── raspberry/        # Raspberry Pi (deployed by CI)
│   │   ├── roles/
│   │   │   ├── beszel_agent/ # Beszel agent monitoring client
│   │   │   ├── common/      # Common system setup (watchdog, kernel panic config, etc.)
│   │   │   ├── docker_apps/ # Docker application deployments
│   │   │   └── thermal_cam/ # Pi Zero thermal camera setup and service
│   │   ├── group_vars/
│   │   ├── hosts
│   │   └── site.yml
│   ├── linux_laptop/     # Linux laptop (manual run, TLP battery thresholds)
│   └── wsl/              # WSL (manual run)
├── scripts/              # Python scripts for various tasks
│   ├── backup_goodreads_web/
│   ├── backup_imdb_web/
│   ├── github_quotas/    # deprecated
│   ├── light_outage/
│   ├── meteo_data/
│   ├── raspberry_outage/
│   └── rasp_zero_cam/    # Pi Zero thermal camera dashboard
└── .github/workflows/
```

## Ansible

Only `ansible/raspberry/**` is deployed by CI (`ansible.yml`). Other hosts are run manually:

```bash
ansible-playbook -i ansible/linux_laptop/hosts ansible/linux_laptop/site.yml
ansible-playbook -i ansible/wsl/hosts ansible/wsl/site.yml
```

## Features

### Backup Scripts

- **Goodreads Backup**: weekly backup of reading lists, see [scripts/backup_goodreads_web/README.md](scripts/backup_goodreads_web/README.md)
- **IMDb Backup**: custom lists backup (monthly), see [scripts/backup_imdb_web/README.md](scripts/backup_imdb_web/README.md)

### Monitoring & Alerts

- **Thermal Camera Dashboard**: MLX90640 thermal camera web stream, BME280 telemetry, and power outage tracking (`thermal_cam` role, `rasp_zero_cam`)
- **System Monitoring (Beszel)**: lightweight system metrics via `beszel_agent` role and Docker container
- **Power Outage Monitoring**: scheduled power outages with Telegram alerts
- **Internet Connectivity**: alerts on Pi internet outages
- **Telegram Notifications**: automatic failure notifications for all workflows

### Infrastructure Management

- **Ansible Playbooks**: server configuration and Docker app deployment
- **Watchdog Setup**: hardware watchdog for automatic reboot on hangs
- **Cron Management**: cron job setup for scheduled tasks

## Deployment

Deployment is configured via `tailscale/github-action`, giving CI access to the Tailscale network. Requires:

- **Ephemeral Authentication Key** (90 days recommended), stored as `TAILSCALE_AUTHKEY` secret
- **OAuth Credentials** (for the Ansible workflow): `TS_OAUTH_CLIENT_ID` and `TS_OAUTH_SECRET`

A regular authentication key also works, but then nodes must be removed manually.

### Required GitHub Secrets

See [.github/workflows/README.md](.github/workflows/README.md) for the complete list.

## Getting Started

1. Clone the repository
2. Configure GitHub Secrets in repository settings
3. Push changes to trigger deployment workflows
4. Scheduled workflows run automatically