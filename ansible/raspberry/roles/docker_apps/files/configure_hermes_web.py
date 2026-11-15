"""Enable web access without replacing provider or messaging settings."""
import os
from pathlib import Path
import secrets
import sys

sys.path.insert(0, '/opt/hermes')
from dotenv import dotenv_values, set_key
from hermes_yaml import safe_dump, safe_load

os.umask(0o077)
home = Path('/opt/data')
env_path = home / '.env'
env_path.touch(mode=0o600, exist_ok=True)
values = dotenv_values(env_path)
changed = False
for name, factory in {
    'HERMES_DASHBOARD_BASIC_AUTH_USERNAME': lambda: 'hermes',
    'HERMES_DASHBOARD_BASIC_AUTH_PASSWORD': lambda: secrets.token_urlsafe(24),
    'HERMES_DASHBOARD_BASIC_AUTH_SECRET': lambda: secrets.token_hex(32),
}.items():
    if not values.get(name):
        set_key(str(env_path), name, factory())
        changed = True
env_path.chmod(0o600)
if values.get('TERMINAL_ENV') != 'local':
    set_key(str(env_path), 'TERMINAL_ENV', 'local')
    changed = True

config_path = home / 'config.yaml'
config = safe_load(config_path.read_text()) if config_path.exists() else {}
config = config or {}
webapp = config.setdefault('webapp', {})
for name, value in {
    'enabled': True, 'host': '0.0.0.0', 'port': 9120, 'public_url': sys.argv[1],
}.items():
    if webapp.get(name) != value:
        webapp[name] = value
        changed = True
dashboard = config.setdefault('dashboard', {})
for name, value in {
    'public_url': sys.argv[2], 'trusted_proxies': [sys.argv[3]],
}.items():
    if dashboard.get(name) != value:
        dashboard[name] = value
        changed = True
terminal = config.setdefault('terminal', {})
if terminal.get('backend') != 'local':
    terminal['backend'] = 'local'
    changed = True
if changed:
    if config_path.exists():
        backup = home / 'config.before-ansible-web.yaml'
        if not backup.exists():
            backup.write_bytes(config_path.read_bytes())
    temporary = home / 'config.yaml.ansible-tmp'
    temporary.write_text(safe_dump(config, sort_keys=False))
    temporary.replace(config_path)
print('changed' if changed else 'ok')
