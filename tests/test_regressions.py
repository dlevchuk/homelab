"""Offline regression checks: python3 -m unittest discover -s tests -v."""
import ast
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
WIFI = ROOT / 'ansible/raspberry/roles/wifi/templates/wifi-fallback.sh.j2'
BACKUP = ROOT / 'ansible/raspberry/roles/docker_apps/templates/docker-backup.sh.j2'
THERMAL = ROOT / 'ansible/raspberry/roles/thermal_cam/files/thermal_web.py'


def shell_function(source, name):
    start = source.index(name + '() {')
    return source[start:source.index('\n}', start) + 2]


def python_def(path, name, namespace):
    tree = ast.parse(path.read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), namespace)
    return namespace[name]


class WifiTests(unittest.TestCase):
    def test_connected_client_is_recognized(self):
        source = WIFI.read_text()
        script = '''
NMCLI=mock_nmcli
WLAN_IF=wlan0
AP_CON_NAME=hotspot-ap
IW=/nonexistent
mock_nmcli() {
  case "$*" in
    '-g GENERAL.STATE dev show wlan0') echo '100 (connected)';;
    '--escape no -g GENERAL.CONNECTION dev show wlan0') echo 'netplan-wlan0-home';;
    *) return 1;;
  esac
}
'''
        script += shell_function(source, 'is_ap_active') + '\n'
        script += shell_function(source, 'is_wifi_client_connected') + '\n'
        script += 'is_wifi_client_connected\n'
        subprocess.run(['bash'], input=script, text=True, check=True)

    def test_zero_hotspot_clients_is_single_integer(self):
        with tempfile.TemporaryDirectory() as directory:
            iw = Path(directory) / 'iw'
            iw.write_text('#!/bin/sh\nexit 0\n')
            iw.chmod(0o755)
            script = f'IW={shlex.quote(str(iw))}\nWLAN_IF=wlan0\n'
            script += shell_function(WIFI.read_text(), 'get_ap_client_count')
            script += '\nget_ap_client_count\n'
            result = subprocess.run(['bash'], input=script, text=True, capture_output=True, check=True)
            self.assertEqual(result.stdout, '0\n')

    def test_hotspot_is_not_mistaken_for_client_wifi(self):
        source = WIFI.read_text()
        script = """
NMCLI=mock_nmcli
WLAN_IF=wlan0
AP_CON_NAME=hotspot-ap
IW=/nonexistent
mock_nmcli() {
  case "$*" in
    '-g GENERAL.STATE dev show wlan0') echo '100 (connected)';;
    '--escape no -g GENERAL.CONNECTION dev show wlan0') echo 'hotspot-ap';;
    *) return 1;;
  esac
}
"""
        script += shell_function(source, 'is_ap_active') + '\n'
        script += shell_function(source, 'is_wifi_client_connected') + '\n'
        script += 'is_wifi_client_connected\n'
        result = subprocess.run(['bash'], input=script, text=True)
        self.assertNotEqual(result.returncode, 0)


class BackupTests(unittest.TestCase):
    def test_restart_failure_preserves_recovery_file(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'stopped'
            state.write_text('abc def\n')
            script = 'STOPPED="abc def"\nSTOPPED_FILE=' + shlex.quote(str(state)) + '\n'
            script += 'docker() { return 1; }\n'
            script += shell_function(BACKUP.read_text(), 'start_containers')
            script += '\nstart_containers\n'
            result = subprocess.run(['bash'], input=script, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(state.read_text(), 'abc def\n')

    def test_successful_restart_clears_recovery_file(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'stopped'
            state.write_text('abc\n')
            script = 'STOPPED=abc\nSTOPPED_FILE=' + shlex.quote(str(state)) + '\n'
            script += 'docker() { return 0; }\n'
            script += shell_function(BACKUP.read_text(), 'start_containers')
            script += '\nstart_containers\n'
            subprocess.run(['bash'], input=script, text=True, check=True)
            self.assertFalse(state.exists())


class ThermalTests(unittest.TestCase):
    def test_database_connections_close_after_success_and_failure(self):
        import os
        import sqlite3
        import threading
        from contextlib import contextmanager
        tree = ast.parse(THERMAL.read_text())
        node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'ThermalDB')
        namespace = dict(os=os, sqlite3=sqlite3, threading=threading, contextmanager=contextmanager)
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(THERMAL), 'exec'), namespace)
        with tempfile.TemporaryDirectory() as directory:
            db = namespace['ThermalDB'](str(Path(directory) / 'test.db'))
            with db._connect() as connection:
                connection.execute('SELECT 1')
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute('SELECT 1')
            with self.assertRaises(RuntimeError):
                with db._connect() as failed_connection:
                    raise RuntimeError('simulated request failure')
            with self.assertRaises(sqlite3.ProgrammingError):
                failed_connection.execute('SELECT 1')

    def test_failed_fetch_never_overwrites_daily_outage(self):
        from datetime import datetime
        import re
        import threading
        db = Mock()
        namespace = dict(
            time=Mock(time=lambda: 1000), get_kyiv_now=lambda: datetime(2026, 11, 12, 12),
            OUTAGE_CACHE_LOCK=threading.Lock(), OUTAGE_FETCH_LOCK=threading.Lock(),
            OUTAGE_CACHE={'data': None}, _outage_cache_valid=lambda *args: False,
            determine_outage_url=lambda *args: ('https://example.test', 'home'),
            reverse_geocode_location=lambda *args: {}, fetch_energy_html=lambda *args: '',
            re=re, _recompute_outage_status=lambda data, now: data,
            save_outage_cache=lambda: None, thermal_db=db,
        )
        fetch = python_def(THERMAL, 'fetch_outage_data', namespace)
        result = fetch(50, 30)
        self.assertFalse(result['ok'])
        db.upsert_outage_daily.assert_not_called()


if __name__ == '__main__':
    unittest.main()
