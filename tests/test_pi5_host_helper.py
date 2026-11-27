"""The root helper accepts only telemetry reads and three fixed fan commands."""
import ast
import io
import json
from pathlib import Path
import subprocess
import unittest
from http.server import BaseHTTPRequestHandler
from unittest.mock import Mock

SCRIPT = Path(__file__).resolve().parents[1] / 'ansible/raspberry/roles/pi5_dashboard/files/pi5_host_helper.py'


class HostHelper(unittest.TestCase):
    def setUp(self):
        tree = ast.parse(SCRIPT.read_text())
        handler = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Handler')
        self.command = Mock()
        self.metrics = Mock(return_value={'fan_rpm': 1200})
        namespace = dict(BaseHTTPRequestHandler=BaseHTTPRequestHandler, json=json,
                         subprocess=Mock(run=self.command, SubprocessError=subprocess.SubprocessError),
                         read_metrics=self.metrics)
        exec(compile(ast.Module(body=[handler], type_ignores=[]), str(SCRIPT), 'exec'), namespace)
        self.handler = namespace['Handler']

    def request(self, payload, path='/fan'):
        data = json.dumps(payload).encode()
        request = Mock(path=path, headers={'Content-Length':str(len(data))}, rfile=io.BytesIO(data))
        self.handler.do_POST(request)
        return request

    def test_fixed_commands(self):
        for mode in ('auto','on','off'):
            self.request({'mode':mode}).reply.assert_called_with({'ok': True})
            self.command.assert_called_with(['/usr/local/sbin/pi5-fan-control',mode],check=True,timeout=3)

    def test_invalid_commands_never_execute(self):
        for payload in ({'mode':'on; reboot'}, {'mode':'/bin/sh'}, [], {}, {'mode':False}):
            self.request(payload).reply.assert_called_with({'error':'Invalid fan command'},400)
        self.request({'mode':'on'}, '/other').reply.assert_called_with({'error':'Not found'},404)
        self.command.assert_not_called()

    def test_command_failure_is_reported(self):
        self.command.side_effect = subprocess.CalledProcessError(1, 'fan')
        self.request({'mode':'auto'}).reply.assert_called_with({'error':'Fan control unavailable'},503)

    def test_status_has_no_hardware_side_effect(self):
        request=Mock(path='/status')
        self.handler.do_GET(request)
        request.reply.assert_called_with({'fan_rpm':1200})
        self.command.assert_not_called()
