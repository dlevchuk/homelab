# Homelab

Home infrastructure managed with Ansible: a Raspberry Pi fleet, Docker services,
monitoring and configuration for Windows, Linux and WSL hosts.

![CI](https://github.com/dlevchuk/homelab/actions/workflows/ci.yml/badge.svg)
![Power alerts](https://github.com/dlevchuk/homelab/actions/workflows/power.yml/badge.svg)
![Ansible deploy](https://github.com/dlevchuk/homelab/actions/workflows/ansible.yml/badge.svg)

## Start here

[All documentation](docs/index.md)

- [Deploy or update a host](docs/deployment.md) — GitHub Secrets, manual runs, CI and local checks.
- [Configure Wi-Fi](docs/wifi.md) — client networks and the fallback hotspot through `WIFI_CONFIG`.
- [Back up or restore a Pi](docs/backup-restore.md) — Docker data, recovery after power loss and SD-card replacement.

Only `ansible/raspberry/**` is deployed automatically after changes reach `main`.
To deploy one Pi manually, open **Actions → Run Ansible Playbook**, choose its
`limit` (`rpi-pi4`, `rpi-zero` or `rpi-pi5`), and use `check` for a dry run.
Other hosts are managed manually; see the [deployment guide](docs/deployment.md#ansible).

## Services and operations

| Area | Documentation |
| --- | --- |
| Pi 5 dashboard | [Glance: configuration and rollback](docs/glance.md) |
| Pi 5 assistant | [Hermes: setup and personal Telegram integration](docs/hermes.md) |
| Wishlist monitoring | [Changedetection.io and price synchronization](docs/wishlist.md) |
| Thermal camera | [MLX90640 dashboard, BME280 telemetry and cloud sync](docs/thermal-camera.md) |
| Power and internet recovery | [Reboot alerts, delivery behavior and limitations](docs/outage-notify.md) |
| Scheduled power outages | [Power alerts](docs/power-alerts.md) |
| Log retention | [Journal, rsyslog and Docker limits](docs/logging.md) |
| Windows | [Host preparation, applications and manual deployment](docs/windows.md) |
| Weather | [Meteo scripts](docs/meteo.md); workflow retained but disabled |

The fleet also includes Beszel metrics, a hardware watchdog and Docker applications
managed by the `docker_apps` role. Disabling an application removes its managed
container while retaining data; see [application lifecycle](docs/deployment.md#disabling-docker-applications).

## Repository layout

```text
ansible/
  raspberry/       Raspberry Pi inventory, group variables and roles
  windows/         Windows configuration
  linux_laptop/    Linux laptop configuration
  wsl/             WSL configuration
scripts/
  light_outage/    Scheduled power-outage alerts
  meteo_data/      Weather scripts
  deprecated/      Retired integrations and historical backups
tests/             Offline Python regression tests
docs/              All infrastructure, service and archived guides
.github/workflows/ CI, deployment, power alerts and disabled meteo workflow
```

## Automation

- **CI:** one run per PR update, with checks selected by changed files. New commits
  cancel outdated runs; the weekly schedule runs the security audit only.
- **Deployment:** Raspberry Pi changes on `main`, or manual execution; running
  deployments are allowed to finish.
- **Power alerts:** daily text summaries from public Chernihivoblenergo JSON, with manual execution available through GitHub Actions.
- **Telegram:** failure notifications within deployment and power jobs.
- **File checks:** formatting and secret detection through pre-commit.ci.

See [workflow details and required checks](docs/deployment.md#workflows).

## Retired integrations

Goodreads, IMDb, Feedly and GitHub quotas are retained under
[the archive guide](docs/deprecated/index.md), together with historical backups.
Their workflows were removed; Git history preserves them. Use manual CSV export
for Goodreads and IMDb.
