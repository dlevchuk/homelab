# Raspberry Pi 5 sensor dashboard

The `pi5_dashboard` Ansible role runs `pi5-dashboard.service` on the Pi 5. Open `http://<pi5-host>:8080/` to view system metrics, the GPIO pinout and PIR activity, or to control the LED strip and buzzer.

The Pi 5 inventory enables the role with `enable_pi5_dashboard` and sets the port with `pi5_dashboard_port`. The service binds to all interfaces. Dashboard access and hardware controls require no sign-in.

| Device | Signal | Physical pin |
| --- | --- | --- |
| WS2813 LEDs | SPI0 MOSI (BCM GPIO10) | 19 |
| MH-FMD buzzer | BCM GPIO24 | 18 |
| HC-SR505 PIR | BCM GPIO25 | 22 |

The SC1148 cooler gets +5 V and GND from the breadboard rails. The YwRobot
545043 powers the +5 V rail, and physical pin 20 provides the common
ground with the Pi 5. Physical pin 18 (BCM GPIO24) carries the buzzer signal.
The cooler uses physical pin 12 (BCM GPIO18) for PWM and physical pin 16
(BCM GPIO23) for tach. Tachometer readings are currently unavailable.

The 40-pin view shows live GPIO modes and levels from Raspberry Pi OS
`pinctrl`. Hover, focus, or tap a pin for details. Physical pins 2 and 4
have empty labels in this view because the breadboard has its own +5 V supply.

The SC1148 card has an on/off button. On requests the maximum cooling state.
Off requests state zero only below the first configured cooling threshold (45 °C by default).
Both commands set the current PWM fan state, and the kernel can change it as
temperature changes. The breadboard keeps supplying the fan with +5 V.

The role installs `python3-libgpiod` and `python3-spidev`. When it enables SPI in the boot configuration, Ansible reboots the Pi to apply the change. The dashboard uses the RP1 GPIO controller and `/dev/spidev0.0`.

If GPIO or SPI cannot initialize, the status page remains available and the affected controls report the missing interface. Check the service status and logs on the Pi:

```sh
systemctl status pi5-dashboard
journalctl -u pi5-dashboard -b
```
