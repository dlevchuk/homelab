"""Host-only telemetry; importing this module never acquires GPIO lines."""
from pathlib import Path
import subprocess

def get_gpio_status():
    try:
        output = subprocess.check_output(
            ["/usr/bin/pinctrl", "get", "0-27"], text=True, timeout=1,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return {}

    states = {}
    for line in output.splitlines():
        pin, separator, description = line.partition(":")
        config, divider, signal = description.partition("|")
        fields = config.split()
        levels = signal.split()
        if separator and divider and pin.strip().isascii() and pin.strip().isdigit() and fields and levels:
            mode = fields[0].lower()
            level = levels[0].lower()
            if mode.isascii() and mode.isalnum() and level in {"hi", "lo", "--"}:
                states[int(pin)] = {"mode": mode, "level": level}
    return states


def read_metrics():
    # CPU temperature
    cpu_temp = None
    try:
        cpu_temp_mc = int(Path("/sys/class/thermal/thermal_zone0/temp").read_text())
        cpu_temp = f"{cpu_temp_mc / 1000:.1f}"
    except (OSError, ValueError):
        try:
            output = subprocess.check_output(["vcgencmd", "measure_temp"], text=True, timeout=1)
            cpu_temp = output.replace("temp=", "").replace("'C", "").strip()
        except (OSError, subprocess.SubprocessError):
            pass

    # 2. Fan status
    fan_state = None
    fan_pwm = None
    fan_rpm = None
    try:
        fans = [
            device for device in Path("/sys/class/thermal").glob("cooling_device*")
            if (device / "type").read_text().strip() == "pwm-fan"
        ]
        if len(fans) == 1:
            fan_state = int((fans[0] / "cur_state").read_text())
    except (OSError, ValueError):
        pass
    try:
        for device in Path("/sys/class/hwmon").glob("hwmon*"):
            if (device / "name").read_text().strip() == "pwmfan":
                fan_pwm = int((device / "pwm1").read_text())
                fan_rpm = int((device / "fan1_input").read_text())
                break
    except (OSError, ValueError):
        pass

    # 3. Uptime
    uptime_str = "-"
    try:
        with open('/proc/uptime', 'r') as f:
            secs = float(f.read().split()[0])
            mins = int(secs // 60)
            hrs = mins // 60
            mins = mins % 60
            uptime_str = f"{hrs}г {mins}хв"
    except: pass

    # 4. Throttling
    throttled = "0x0"
    try:
        out = subprocess.check_output(['vcgencmd', 'get_throttled'], text=True)
        throttled = out.replace("throttled=", "").strip()
    except: pass

    return {"gpio": get_gpio_status(), "cpu_temp": cpu_temp,
            "fan_state": fan_state, "fan_pwm": fan_pwm, "fan_rpm": fan_rpm,
            "uptime": uptime_str, "throttled": throttled}
