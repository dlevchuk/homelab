"""Run the actual watchdog with harmless command stubs and persistent state."""
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'ansible/raspberry/roles/common/templates/ping-reboot-check.sh.j2'


class PingRebootTests(unittest.TestCase):
    def test_only_one_reboot_until_internet_recovers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fail, marker, reboots = (root / name for name in ('fails', 'marker', 'reboots'))
            source = SCRIPT.read_text()
            for old, new in [('PING=/usr/bin/ping', 'PING=mock_ping'),
                             ('LOGGER=/usr/bin/logger', 'LOGGER=true'),
                             ('REBOOT=/sbin/reboot', 'REBOOT=mock_reboot'),
                             ('IP=/usr/bin/ip', 'IP=mock_ip'),
                             ('FAIL_FILE=/tmp/ping_reboot_fail_count', f'FAIL_FILE={fail}'),
                             ('REBOOT_MARKER=/var/lib/ping-reboot/reboot-requested', f'REBOOT_MARKER={marker}'),
                             ('MIN_UPTIME_SEC=600', 'MIN_UPTIME_SEC=0')]:
                source = source.replace(old, new)
            # Disable local-machine Wi-Fi tools; no host network or reboot is touched.
            source = source.replace('command -v iw', 'false').replace('command -v nmcli', 'false')
            prelude = f'''mock_ping() {{ return "$NETWORK_STATUS"; }}
mock_ip() {{ return 0; }}
mock_reboot() {{ echo reboot >> '{reboots}'; }}
sync() {{ return 0; }}
'''
            def run(status=1):
                subprocess.run(['bash'], input=f'NETWORK_STATUS={status}\n' + prelude + source,
                               text=True, check=True)
            for _ in range(3):
                run()
            self.assertTrue(marker.exists())
            self.assertEqual(reboots.read_text().count('reboot'), 1)
            fail.unlink(missing_ok=True)  # simulate reboot clearing /tmp
            for _ in range(6):
                run()
            self.assertEqual(reboots.read_text().count('reboot'), 1)
            run(0)
            self.assertFalse(marker.exists())
            for _ in range(3):
                run()
            self.assertEqual(reboots.read_text().count('reboot'), 2)
