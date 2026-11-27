#!/usr/bin/python3
"""Local socket: host telemetry and three fixed fan commands only."""
import json
import os
import socketserver
import subprocess
from http.server import BaseHTTPRequestHandler
from pi5_host_metrics import read_metrics

SOCKET = '/run/pi5-dashboard-host/api.sock'


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/status':
            self.reply(read_metrics())
        else:
            self.reply({'error': 'Not found'}, 404)

    def do_POST(self):
        if self.path != '/fan':
            self.reply({'error': 'Not found'}, 404)
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 128:
                raise ValueError('Invalid length')
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict) or payload.get('mode') not in ('auto', 'on', 'off'):
                raise ValueError('Invalid mode')
        except (ValueError, UnicodeError):
            self.reply({'error': 'Invalid fan command'}, 400)
            return
        try:
            subprocess.run(['/usr/local/sbin/pi5-fan-control', payload['mode']], check=True, timeout=3)
        except (OSError, subprocess.SubprocessError):
            self.reply({'error': 'Fan control unavailable'}, 503)
            return
        self.reply({'ok': True})

    def reply(self, data, status=200):
        content = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, *args):
        pass


class Server(socketserver.UnixStreamServer):
    def get_request(self):
        request, _ = super().get_request()
        request.settimeout(5)
        return request, ('localhost', 0)


if __name__ == '__main__':
    if os.path.exists(SOCKET):
        os.unlink(SOCKET)
    with Server(SOCKET, Handler) as server:
        os.chmod(SOCKET, 0o660)
        server.serve_forever()
