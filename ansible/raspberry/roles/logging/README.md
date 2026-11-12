# Fleet log retention

The `logging` role runs on every host in `raspberry`, including Pi Zero.
Defaults can be overridden in group_vars or host_vars.

| Logs | Default policy |
| --- | --- |
| Persistent systemd journal | 100 MiB target, up to 7 days, 10 MiB journal files, compression |
| Free disk space for other uses | journald stops growing when less than 500 MiB remains |
| Runtime journal (early boot / unavailable disk) | 20 MiB |
| rsyslog text logs, if its package policy exists | daily rotation, 10 MiB size threshold, 7 archives, maxage 7 days, compression |
| Ansible-managed Docker stdout/stderr | json-file, 10 MiB × 3 files per container, compressed archives |

One week is a practical starting point for diagnosing failures on SD-card-based
Pis without keeping months of logs. Size limits take precedence over preserving
a full week. Journald limits are approximate because active journal files cannot
be vacuumed. The first configuration change restarts journald and rotates/vacuums
existing journals; later runs only do this when the configuration changes.

The rsyslog policy replaces `/etc/logrotate.d/rsyslog` without adding duplicate
paths. Other package-specific rotation policies remain in effect. Logrotate checks
size only when its daily timer runs, so 10 MiB is a rotation threshold, not a hard
cap. `maxage` is checked when a file is rotated; inactive archives can remain older
than seven days. There is no blanket rule over `/var/log/*` that could interfere
with package-specific postrotate hooks, binary logs or journals.

Docker defaults are applied through `module_defaults` in `site.yml`, including to
existing managed containers. The next full deployment may recreate containers to
change their log configuration, briefly interrupting their services and discarding
old container logs. Volumes keep their data. Docker retention is size-based;
quiet containers can retain logs older than seven days. Containers created outside
this playbook and application logs written inside volumes need their own policies.
No Docker daemon restart or daemon configuration change is needed.

Main variables are in [defaults/main.yml](defaults/main.yml).

Apply just the system logging role (does not update Docker containers):

```sh
ansible-playbook -i ansible/raspberry/hosts ansible/raspberry/site.yml --tags logging --check --diff
ansible-playbook -i ansible/raspberry/hosts ansible/raspberry/site.yml --tags logging
```

Use a full playbook run to apply container limits. Inspect on a Pi:

```sh
journalctl --disk-usage
systemd-analyze cat-config systemd/journald.conf
sudo logrotate --debug /etc/logrotate.conf
systemctl status logrotate.timer
docker inspect --format '{{json .HostConfig.LogConfig}}' homepage
```

References: [journald](https://www.freedesktop.org/software/systemd/man/258/journald.conf.d.html),
[logrotate](https://manpages.debian.org/bookworm/logrotate/logrotate.conf.5.en.html),
[Docker json-file](https://docs.docker.com/engine/logging/drivers/json-file/).
