# Glance dashboard (Pi 5)

[← Repository overview](../README.md)

Pi 5 runs Glance at `http://pi5.tail72e9f.ts.net:3005`, replacing Homepage.
The old Homepage configuration directory is retained for rollback; Pi 4 keeps
its existing Homepage dashboard. The forest-green theme,
weather for Odesa, clock, calendar, Docker status, service checks, bookmarks,
release feed and browser-local to-do list are managed by Ansible. The Applications
section links to [Pi 5 Sensors](http://pi5.tail72e9f.ts.net:8080/) when the
[GPIO dashboard](pi5-dashboard.md) is enabled.

```bash
ansible-playbook -i ansible/raspberry/hosts ansible/raspberry/site.yml \
  --limit rpi-pi5 --tags glance \
  -e ansible_ssh_private_key_file="$HOME/.ssh/raspberry"
```

The role renders `/home/pi/docker/glance/glance.yml` from
`templates/glance/glance.yml.j2`, reusing the Homepage service/bookmark templates
as the source of links and enabled-service conditions. Edit the source templates
and rerun Ansible; direct edits on the Pi are overwritten. Glance automatically
reloads its configuration. Theme colors, image, port and tracked release
repositories are configurable via the `glance_*` role defaults.

Server stats use the host PID namespace and a read-only root mount at
`/host/root`; CPU is load average relative to core count, rather than the
instantaneous utilization Homepage shows. Temperature depends on sensor
detection. The Docker widget shows container status, not Homepage's per-container
CPU/RAM/network values; Glances and Beszel remain available for detailed metrics.
To-do items live in each browser's local storage and are not synced or included
in server backups. Disable Glance with `enable_glance: false` and run the full
role to remove its container while retaining the configuration directory.

Glance and Hermes share an internal Docker network, `glance-hermes`, for
the monitor's `http://hermes:9120/` check. Both retain their original bridge
network and published ports. This avoids routing through Tailscale Serve
from a Docker bridge; the clickable Hermes link remains HTTPS.

To roll Pi 5 back to Homepage, set `enable_homepage: true`,
`enable_glance: false` in its group variables and run the full `docker_apps`
role (the full playbook handles disabled-container cleanup before starting
Homepage). The trial Glance port 3006 is no longer published.
