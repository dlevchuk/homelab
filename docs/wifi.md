# Wi-Fi Configuration

[← Repository overview](../README.md)

The `wifi` role gives a Pi several client Wi-Fi networks plus a fallback hotspot. It is **off by default**: the role runs only where `enable_wifi: true` is set in `ansible/raspberry/group_vars/<group>.yml` (enabled for pi4 and pi5). Both use NetworkManager and the same `WIFI_CONFIG` format. NetworkManager is the only supported backend.

If neither Ethernet nor known Wi-Fi networks are connected after boot (with a 3-minute grace period to let routers boot up after power outages), the Pi activates a fallback AP on `10.42.0.1` (`ipv4.method shared`). A per-minute systemd timer (`wifi-fallback.timer`) periodically retries client networks every 5 minutes if no devices are connected to the hotspot.

The role does not migrate a Pi from dhcpcd / wpa_supplicant to NetworkManager. That is done by hand once, before the first run of the role. On the NetworkManager backend the role installs `dnsmasq-base`, `python3-gi` and `gir1.2-nm-1.0` (needed by the `nmcli` Ansible module).

Pi 5 with Raspberry Pi OS Trixie already uses NetworkManager. Existing netplan-generated NetworkManager connections (such as `netplan-wlan0-<ssid>`) are preserved; the role adds its `wifi-<ssid>` profiles without restarting NetworkManager or applying netplan. The global `wpa_supplicant` service can remain active because NetworkManager uses it for Wi-Fi authentication.

## `WIFI_CONFIG` secret

Networks and the hotspot password are not stored in git. They come from the GitHub Secret `WIFI_CONFIG`, a single JSON object that CI exports as an environment variable:

```json
{
  "ap_password": "<min-8-chars>",
  "networks": [
    { "ssid": "Home-5G",        "psk": "<wifi-password>" },
    { "ssid": "Backup-Hotspot", "psk": "<wifi-password>" },
    { "ssid": "Hidden-Network", "psk": "<wifi-password>", "hidden": true },
    { "ssid": "Open-Network" }
  ]
}
```

| Field | Type | Required | Description |
| :--- | :--- | :---: | :--- |
| `ap_password` | string | no | WPA2 password of the fallback hotspot, 8-63 characters. If empty or missing, the hotspot profile is not created |
| `networks` | array | no | Client networks. If empty or missing, no client profiles are created |
| `networks[].ssid` | string | yes | Network name. Always a JSON **string**, so numeric names must be quoted (`"49"`, not `49`) |
| `networks[].psk` | string | no | WPA2 password. If empty or missing, the network is configured as open |
| `networks[].hidden` | boolean | no | `true` for a hidden SSID, default `false` |

Rules and behaviour:

- It must be valid JSON: no trailing commas, lowercase `true` / `false`, double quotes only, and `"` or `\` inside a password must be escaped. Validate before saving `WIFI_CONFIG`: `jq -c . wifi.json` prints a compact one-line JSON or the error (do not commit the file).
- All networks have the same priority (`wifi_client_priority`, 10): the Pi connects to whichever known network is available.
- Profiles are named `wifi-<ssid>` and are created or updated by Ansible. Connections that were not created by the role are left alone.
- The hotspot SSID is `<inventory_hostname>-ap` (for example `rpi-pi4-ap`), its address is `10.42.0.1/24`, and a connected client reaches the Pi with `ssh pi@10.42.0.1`.
- If no Wi-Fi secrets are set, the role skips client and hotspot profile creation, but still installs dependencies and deploys the fallback timer and script. Existing profiles are preserved.

To change networks: update the secret, then run Actions → `Run Ansible Playbook` with `limit` for that Pi (first with `check`, if you want to see the diff).
