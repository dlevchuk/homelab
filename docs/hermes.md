# Hermes Agent (Pi 5)

[← Repository overview](../README.md)

The `docker_apps` role installs the official ARM64-compatible
`nousresearch/hermes-agent:stable` image on pi5 only. State, credentials and
sessions persist in `/home/pi/docker/hermes` and are included in Docker backups.
Watchtower updates are disabled for this container. The Docker socket is not
mounted. Web ports 9119 (settings dashboard) and 9120 (chat) bind to host
loopback. Tailscale Serve exposes them over HTTPS only within the tailnet:
443 for the dashboard and 8443 for chat. This requires Tailscale Serve/HTTPS
to be enabled for the tailnet. `hermes_trusted_proxy` must match the Docker
bridge gateway (`docker network inspect bridge`); pi5 uses `172.17.0.1`.
Terminal tools use the local backend inside the Hermes container.
Pi5 pins the main model to `gemini-3.5-flash` through `hermes_model` in its group
variables. Gemini 3.8 Flash stalled before its first response in deployment
checks; change the pin only after validating both an API response and Hermes
tool execution. API credentials remain in the private `.env` file.
See the [official Docker guide](https://hermes-agent.nousresearch.com/docs/user-guide/docker).

Deploy only Hermes from the repository root (override the inventory's legacy key path):

```bash
ansible-playbook -i ansible/raspberry/hosts ansible/raspberry/site.yml \
  --limit rpi-pi5 --tags hermes \
  -e ansible_ssh_private_key_file="$HOME/.ssh/raspberry"
```

Pi5 runs `gateway run` with the dashboard and Webapp enabled. Ansible generates
a persistent random login in `/home/pi/docker/hermes/.env` on first deployment.
Open `https://pi5.tail72e9f.ts.net` through Tailscale to configure providers
and messaging; `https://pi5.tail72e9f.ts.net:8443` serves the chat interface.
The login username is `hermes`; retrieve the generated password on pi5:

```bash
sudo sed -n '/^HERMES_DASHBOARD_BASIC_AUTH_PASSWORD=/p' /home/pi/docker/hermes/.env
```

Configure a dedicated Telegram bot and allowed Telegram user IDs. Do not reuse
the outage notification bot. An interactive setup wizard is also available.
For Gemini, select **Google AI Studio / Gemini** and supply a Google AI Studio
API key (`GOOGLE_API_KEY`, also recognized as `GEMINI_API_KEY`). Select an available
model in the wizard. See the [provider reference](https://hermes-agent.nousresearch.com/docs/integrations/providers).

```bash
ssh -i ~/.ssh/raspberry pi@pi5.tail72e9f.ts.net
docker exec -it hermes hermes setup
```

Use `docker exec hermes hermes gateway status` and a message from an allowed
Telegram account to verify the gateway. Ansible leaves wizard-managed credentials
and other configuration intact on subsequent runs, merging web listener, proxy
and container-local terminal settings. Tailscale Serve is configured per port;
unrelated listeners are retained. Disabling Hermes does not delete its existing
Tailscale Serve routes; remove those explicitly if decommissioning the service.

## Personal Telegram account (on-demand sending)

Pi5 also installs Telethon 1.45.0 in an isolated Python environment and a
`personal-telegram` Hermes skill. This is separate from the Bot API gateway.
It has no message listeners or automatic replies. Credentials, the MTProto
session, recipient aliases and a send journal persist under
`/home/pi/docker/hermes/integrations/personal-telegram` with private permissions.
The session grants access to the personal account and is included in the
existing Docker backups; treat those backups as credentials too.

Get your own `api_id` and `api_hash` from `https://my.telegram.org` → API
development tools. Enter these, your phone number, login code and 2FA password
directly in an interactive SSH terminal:

```bash
ssh -i ~/.ssh/raspberry pi@pi5.tail72e9f.ts.net
docker exec -it --user 10000:10000 -e HOME=/opt/data hermes \
  /opt/data/integrations/personal-telegram/venv/bin/python \
  /opt/data/integrations/personal-telegram/client.py login --qr
```

Scan the terminal QR in Telegram Settings → Devices → Link Desktop Device.
Enter the 2FA password in the SSH terminal if requested. QR codes renew up to
three times when expired. To use a login code instead, omit `--qr`.
Stop an older pending login with Ctrl+C before starting a new one.
Only `login` prompts for credentials; other commands fail if not authorized.
The client supports `status`, `contacts --query NAME`,
`add-recipient ALIAS --user @username` (or a numeric user ID returned by
contacts), and `send ALIAS --text-file FILE --request-id UUID`.
To send to a group from the personal account, run `groups --query NAME` to
find a group the account has joined. Copy its full negative ID and register
it with `add-recipient ALIAS --group ID`. Use the same `send` command with
the group alias. Basic groups and supergroups are supported. Telegram
applies the account's existing posting permissions.
Each retry must reuse its original UUID so Telegram can suppress duplicates.
The skill instructs Hermes to send only when the owner explicitly requests the
recipient and message, and to clarify ambiguous recipients or unspecified text.
Use a new Hermes chat after deployment if the new skill is not yet listed.

To revoke the personal-account session, use Telegram Settings → Devices and
terminate **Hermes pi5 personal sender**. Re-login is then required.
