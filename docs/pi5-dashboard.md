# Raspberry Pi 5 sensor dashboard

The `pi5_dashboard` Ansible role runs the ARM64 Docker container `pi5-dashboard` at `http://<pi5-host>:8080/`. The separate `pi5-dashboard-host.service` supplies host telemetry and fixed fan commands through a local Unix socket. The former `pi5-dashboard.service`, standalone script and user sudo rule have been removed. Dashboard access and hardware controls still require no sign-in; network exposure is unchanged.

## Runtime and data

- Image: `local/pi5-dashboard:latest`, built on the Pi from `/opt/pi5-dashboard/Dockerfile` and the role's Python files. Debian Trixie supplies Python, libgpiod v2 and spidev. Ansible uses `docker build` with BuildKit, sharing the cache with manual builds; registry failures are retried three times. `--pull=false` avoids forcing a base-image refresh, but an uncached build still needs network access. The legacy Docker API builder is not used.
- Non-root: host user's UID/GID, supplementary gpio/spi device groups, no added capabilities, no-new-privileges, read-only root filesystem, small `/tmp` tmpfs. No privileged mode, host root, Docker socket or writable `/sys` mount. In Ansible, keep the tmpfs specification as one quoted list item (`"/tmp:rw,noexec,nosuid,size=16m"`); an unquoted YAML flow list splits it at commas and Docker rejects `noexec` as a mount path.
- Devices: only the RP1 gpiochip and `/dev/spidev0.0`. Ansible discovers the chip by `pinctrl-rp1` label and resolves its real path; on the migrated Pi it is `/dev/gpiochip0`. Reapply the role if OS updates renumber GPIO devices.
- History: `/home/pi/docker/pi5-dashboard/motion.sqlite3`, mounted at `/data/motion.sqlite3`. This is the active history database. The native database was removed only after verifying that all its records existed in this database.
- Timezone: `TZ=Europe/Kyiv` inside the container, matching the host’s Kyiv time.
- Restart: Docker `unless-stopped`; HTTP healthcheck checks access to host telemetry. Logs: JSON rotation, 3 × 10 MiB.
- Host helper: sources in `/opt/pi5-dashboard-host`, socket `/run/pi5-dashboard-host/api.sock` with root:pi ownership and mode 0660. The runtime directory is preserved during service restarts so the container's bind mount remains valid. No TCP listener.

The helper accepts `GET /status` and `POST /fan` with only `auto`, `on`, or `off`. It reads real host temperature, uptime, throttling, GPIO modes, fan PWM and RPM. It runs the existing root-owned `/usr/local/sbin/pi5-fan-control`; container requests never supply a shell command. The kernel temperature policy remains independent of Docker.

```sh
docker ps --filter name=pi5-dashboard
docker logs --tail 100 pi5-dashboard
docker inspect pi5-dashboard --format '{{.State.Health.Status}}'
systemctl status pi5-dashboard-host
journalctl -u pi5-dashboard-host -b
curl --unix-socket /run/pi5-dashboard-host/api.sock http://localhost/status
```

## Hardware

| Device | Signal | Physical pin |
| --- | --- | --- |
| WS2813 LEDs | SPI0 MOSI (BCM GPIO10) | 19 |
| MH-FMD buzzer | BCM GPIO24 | 18 |
| HC-SR505 PIR | BCM GPIO25 | 22 |
| SC1148 cooler | PWM BCM GPIO18 / tach BCM GPIO23 | 12 / 16 |
| Breadboard common ground | GND | 20 |

The YwRobot 545043 powers the breadboard's +5 V rail, including the cooler. GPIO18 carries the PWM signal, not the cooler's power supply. The RTC battery uses the separate `J5 / BAT` connector. The GPIO pinout labels physical pins 2 and 4 as 5 V; the breadboard has its own power supply.

Ansible loads `pwm-fan` and the `pi5-fan-gpio18` correction for RP1 PWM0 channel 2 and the falling-edge tachometer on GPIO23, two pulses per revolution. Kernel overlays and boot configuration remain on the host and are not managed inside Docker.

Fan thresholds are 45 / 55 / 65 / 72 °C with 5 °C hysteresis. Each overlay parameter is on a separate short `dtparam` line: a previous long line was truncated by firmware. See the [Raspberry Pi config.txt limit](https://www.raspberrypi.com/documentation/computers/config_txt.html). Current live thresholds were also corrected through sysfs without reboot. Other overlay changes still require reboot.

“Авто” immediately selects the temperature-appropriate cooling state. Maximum/off are temporary state commands; the kernel's `step_wise` policy remains active. Fan status uses tachometer and PWM; the dashboard does not measure the +5 V supply voltage.

## Interface and hardware errors

“Підключення та GPIO” combines the header and breadboard, highlights related modules/pins, and optionally shows all 40 pins. Supply/wiring details are collapsible. GPIO states use readable names; physical pin numbers and BCM GPIO remain separate.

Connection status reflects successful API polling. The initial loading label is shown only before the first response; normal background polling keeps “Підключено” visible without blinking. Errors/timeouts dim stale readings. PIR, GPIO, SPI and tachometer errors are separate; unavailable PIR is not shown as “no motion,” and missing tachometer is distinct from zero RPM. Failed GPIO/SPI initialization requires restarting the container after correcting the underlying device/permission problem.

PIR records rising edges in SQLite, retaining up to 1000 events. The page offers “Остання година” and “Сьогодні,” counts retained events and shows 5-minute/hourly bars plus the latest 20 events. Day boundaries follow the Pi's timezone. The counter “За цей запуск” resets with the process; stored history survives container restart/recreation. History predates Docker when migrated from the original database.

LED modes change only after successful SPI transfer. Transfer failure returns HTTP 503; background effect failure also makes the UI state unconfirmed. WS2813 has no feedback channel: successful SPI transfer does not prove physical illumination. Brightness 0–100% scales output RGB without changing the selected source color and applies to rainbow. Brightness defaults to 100% after restart. Off retains the last source RGB; startup and graceful shutdown send an off frame.

## Deploy and recovery

Reapply the `pi5_dashboard` Ansible role on `rpi-pi5`. It installs the helper, builds the image, discovers device/group IDs and starts the container. On a host that still has the old service, the role stops it before acquiring hardware in Docker, migrates its database only if the destination does not exist, and removes the retired files after the API is ready. The old database is removed only after record verification. On Pi reboot, helper and Docker start automatically; brief startup ordering differences recover on subsequent API polls.

The native fallback and its sudo permissions are retired. Recover the application by rebuilding a known-good repository revision with Ansible, keeping `/home/pi/docker/pi5-dashboard` intact. Restarting/recreating the container must not delete that directory. Keep the host helper and `/usr/local/sbin/pi5-fan-control`: the container requires them for telemetry and fan commands.

Before replacing or restoring history, stop the container and make a consistent SQLite backup. For example, on the Pi:

```sh
docker stop pi5-dashboard
python3 - <<'PYTHON'
from contextlib import closing
import sqlite3
from pathlib import Path
source = Path.home() / 'docker/pi5-dashboard/motion.sqlite3'
target = source.with_name('motion.backup.sqlite3')
with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(target)) as dst:
    src.backup(dst)
PYTHON
docker start pi5-dashboard
```

## Post-migration cleanup

The cleanup removes the retired dashboard unit, its standalone script and development copies, the old fan-control sudo rule, temporary migration/build uploads and unused older dashboard images. The source database is checked against the active database before removal. Docker images used by any container and images belonging to other applications are retained; no global Docker prune is performed.

Keep these live paths:

| Path | Purpose |
| --- | --- |
| `/opt/pi5-dashboard` | Docker build source |
| `/opt/pi5-dashboard-host` | Host helper and telemetry source |
| `/run/pi5-dashboard-host` | Local helper socket |
| `/home/pi/docker/pi5-dashboard` | Persistent PIR history |
| `/etc/systemd/system/pi5-dashboard-host.service` | Host helper autostart |
| `/usr/local/sbin/pi5-fan-control` | Fixed fan commands used by the helper |

The old `pi5-dashboard.service` template and direct host/sudo fallback were also removed from the repository. The container now always uses the local helper socket.
