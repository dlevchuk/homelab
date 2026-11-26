# Raspberry Pi 5 sensor dashboard

The `pi5_dashboard` Ansible role runs `pi5-dashboard.service` on the Pi 5. Open `http://<pi5-host>:8080/` to view system metrics, the GPIO pinout and PIR activity, or to control the LED strip and buzzer.

The Pi 5 inventory enables the role with `enable_pi5_dashboard` and sets the port with `pi5_dashboard_port`. The service binds to all interfaces. Dashboard access and hardware controls require no sign-in.

| Device | Signal | Physical pin |
| --- | --- | --- |
| WS2813 LEDs | SPI0 MOSI (BCM GPIO10) | 19 |
| MH-FMD buzzer | BCM GPIO24 | 18 |
| HC-SR505 PIR | BCM GPIO25 | 22 |

The SC1148 cooler gets +5 V and GND from the breadboard rails. The YwRobot 545043 powers the +5 V rail, and physical pin 6 provides a common ground with the Pi 5. The cooler connects to physical pin 12 (BCM GPIO18) for PWM and pin 16 (BCM GPIO23) for tach.

The role installs `python3-libgpiod` and `python3-spidev`. When it enables SPI in the boot configuration, Ansible reboots the Pi to apply the change. The dashboard uses the RP1 GPIO controller and `/dev/spidev0.0`.

If GPIO or SPI cannot initialize, the status page remains available and the affected controls report the missing interface. Check the service status and logs on the Pi:

```sh
systemctl status pi5-dashboard
journalctl -u pi5-dashboard -b
```
