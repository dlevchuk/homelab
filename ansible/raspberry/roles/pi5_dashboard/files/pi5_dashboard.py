#!/usr/bin/env python3
"""
Raspberry Pi 5 Hardware Sensors & Pinout Web Dashboard
Controls:
- 🌀 Active Cooler SC1148 (PWM GPIO 18, TACH GPIO 23)
- 🔔 MH-FMD Buzzer (GPIO 24)
- 💡 WS2813 RGB LEDs (SPI0 MOSI / GPIO 10)
- 👁️ HC-SR505 PIR Motion Sensor (GPIO 25)
- ⚡ YwRobot 545043 Breadboard Power Map
"""

import os
from pathlib import Path
import sys
import time
import json
import sqlite3
import socket
import signal
import http.client
from contextlib import closing
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

PORT = int(os.environ.get("DASHBOARD_PORT", "8080"))

HOST_SOCKET = os.environ.get("DASHBOARD_HOST_SOCKET", "/run/pi5-dashboard-host/api.sock")


def host_request(method, path, payload=None):
    connection = http.client.HTTPConnection("localhost", timeout=4)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(4)
    try:
        sock.connect(HOST_SOCKET)
        connection.sock = sock
        body = json.dumps(payload).encode() if payload is not None else None
        connection.request(method, path, body, {"Content-Type": "application/json"})
        response = connection.getresponse()
        result = json.loads(response.read())
        if response.status != 200:
            raise OSError(result.get("error", "Host helper error"))
        return result
    finally:
        connection.close()
        sock.close()


# Hardware locks and state
hw_lock = threading.Lock()
pir_count = 0
pir_last_time = "Ніколи"
pir_current_state = False
auto_alarm_enabled = False
led_current_mode = "off"  # "off", "color", "rainbow"
led_color = [0, 0, 0]

led_brightness = 100
led_error = None
led_generation = 0
led_lock = threading.RLock()
gpio_error = None

HISTORY_DB = Path(os.environ.get("PIR_HISTORY_DB", "/data/motion.sqlite3"))


def motion_history(timestamp=None):
    """Keep the last 1000 rising edges, return the latest 20 for the dashboard."""
    HISTORY_DB.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(HISTORY_DB)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS motion (id INTEGER PRIMARY KEY, ts REAL NOT NULL)")
        if timestamp is not None:
            db.execute("INSERT INTO motion(ts) VALUES (?)", (timestamp,))
            db.execute("DELETE FROM motion WHERE id NOT IN (SELECT id FROM motion ORDER BY id DESC LIMIT 1000)")
        return [row[0] for row in db.execute("SELECT ts FROM motion ORDER BY id DESC LIMIT 20")]


def motion_report(period, now=None):
    now = time.time() if now is None else now
    if period == "hour":
        start, bucket_seconds, bucket_count = now - 3600, 300, 12
    elif period == "today":
        local = time.localtime(now)
        start = time.mktime(local[:3] + (0, 0, 0) + local[6:])
        bucket_seconds, bucket_count = 3600, 25
    else:
        raise ValueError("Invalid history period")
    motion_history()  # Initialize the database on first use.
    with closing(sqlite3.connect(HISTORY_DB)) as db, db:
        timestamps = [row[0] for row in db.execute(
            "SELECT ts FROM motion WHERE ts >= ? AND ts <= ? ORDER BY id DESC", (start, now))]
    buckets = [0] * bucket_count
    for timestamp in timestamps:
        index = min(bucket_count - 1, int((timestamp - start) / bucket_seconds))
        buckets[index] += 1
    return {"events": timestamps[:20], "count": len(timestamps), "buckets": buckets,
            "start": start, "bucket_seconds": bucket_seconds}


# Try loading spidev
try:
    import spidev
    spi = spidev.SpiDev()
    spi.open(0, 0)
    spi.max_speed_hz = 6400000
except Exception as e:
    spi = None
    print(f"[SPI] Warning: could not open spidev (0,0): {e}")

# Try loading gpiod
try:
    import gpiod
    for chip_path in Path("/dev").glob("gpiochip*"):
        with gpiod.Chip(str(chip_path)) as chip:
            if chip.get_info().label == "pinctrl-rp1":
                break
    else:
        raise RuntimeError("RP1 GPIO controller not found")
    gpio_req = gpiod.request_lines(
        str(chip_path),
        consumer='pi5_dashboard',
        config={
            25: gpiod.LineSettings(direction=gpiod.line.Direction.INPUT, bias=gpiod.line.Bias.PULL_DOWN),
            24: gpiod.LineSettings(direction=gpiod.line.Direction.OUTPUT)
        }
    )
except Exception as e:
    gpio_req = None
    print(f"[GPIO] Warning: could not initialize gpiod: {e}")

# WS2813 bit encoding at 6.4 MHz
BIT_0 = 0xC0
BIT_1 = 0xFC

def send_ws2813_pixels(pixels, brightness=None):
    """pixels: list of (r, g, b) tuples"""
    global led_error
    if not spi:
        led_error = "SPI недоступний"
        raise OSError(led_error)
    brightness = led_brightness if brightness is None else brightness
    buf = bytearray()
    for (r, g, b) in pixels:
        for byte_val in (g, r, b):
            byte_val = byte_val * brightness // 100
            for bit_pos in range(7, -1, -1):
                buf.append(BIT_1 if (byte_val >> bit_pos) & 1 else BIT_0)
    buf.extend(bytearray([0x00] * 300))
    with hw_lock:
        try:
            spi.xfer2(list(buf))
            led_error = None
        except Exception as e:
            led_error = "Помилка передачі SPI"
            print(f"[SPI] xfer error: {e}", flush=True)
            raise OSError(led_error) from e

def play_tone(freq=2200, duration=0.15):
    """Generate audio frequency square wave for passive buzzer"""
    if not gpio_req:
        return
    half_period = 1.0 / freq / 2.0
    end_time = time.perf_counter() + duration
    while time.perf_counter() < end_time:
        gpio_req.set_value(24, gpiod.line.Value.ACTIVE)
        t = time.perf_counter()
        while time.perf_counter() - t < half_period: pass
        gpio_req.set_value(24, gpiod.line.Value.INACTIVE)
        t = time.perf_counter()
        while time.perf_counter() - t < half_period: pass

def play_buzzer_beep(times=1, freq=2200, duration=0.18, interval=0.08):
    if not gpio_req:
        return
    def _worker():
        with hw_lock:
            try:
                for _ in range(times):
                    play_tone(freq=freq, duration=duration)
                    time.sleep(interval)
            except Exception as e:
                print(f"[Buzzer] error: {e}")
    threading.Thread(target=_worker, daemon=True).start()

def play_buzzer_alarm():
    if not gpio_req:
        return
    def _worker():
        with hw_lock:
            try:
                for freq in [1600, 2200, 2600, 2200, 1600, 2400]:
                    play_tone(freq=freq, duration=0.12)
                    time.sleep(0.02)
            except Exception as e:
                print(f"[Buzzer] alarm error: {e}")
    threading.Thread(target=_worker, daemon=True).start()

def rainbow_pixels(step):
    def wheel(pos):
        pos = 255 - (pos & 255)
        if pos < 85:
            return (255 - pos * 3, 0, pos * 3)
        if pos < 170:
            pos -= 85
            return (0, pos * 3, 255 - pos * 3)
        pos -= 170
        return (pos * 3, 255 - pos * 3, 0)
    return [wheel(step), wheel(step + 128)]


def rainbow_loop(generation):
    step = 4
    while True:
        with led_lock:
            if generation != led_generation or led_current_mode != "rainbow":
                return
            try:
                send_ws2813_pixels(rainbow_pixels(step))
            except OSError:
                return
        step = (step + 4) % 256
        time.sleep(0.04)


def start_rainbow():
    global led_current_mode, led_generation
    with led_lock:
        send_ws2813_pixels(rainbow_pixels(0))
        led_current_mode = "rainbow"
        led_generation += 1
        threading.Thread(target=rainbow_loop, args=(led_generation,), daemon=True).start()


def set_led_solid_color(r, g, b):
    global led_current_mode, led_color, led_generation
    with led_lock:
        send_ws2813_pixels([(r, g, b), (r, g, b)])
        led_generation += 1
        led_current_mode = "color"
        led_color = [r, g, b]


def turn_leds_off():
    global led_current_mode, led_generation
    with led_lock:
        send_ws2813_pixels([(0, 0, 0), (0, 0, 0)])
        led_generation += 1
        led_current_mode = "off"


def set_led_brightness(value):
    global led_brightness, led_generation
    with led_lock:
        pixels = (rainbow_pixels(0) if led_current_mode == "rainbow" else
                  [tuple(led_color)] * 2 if led_current_mode == "color" else [(0, 0, 0)] * 2)
        send_ws2813_pixels(pixels, brightness=value)
        led_brightness = value
        if led_current_mode == "rainbow":
            led_generation += 1
            threading.Thread(target=rainbow_loop, args=(led_generation,), daemon=True).start()

try:
    turn_leds_off()
except OSError:
    pass

# Background worker for HC-SR505 motion tracking
def pir_monitor_thread():
    global pir_count, pir_last_time, pir_current_state, gpio_error
    last_val = False
    while True:
        try:
            if gpio_req:
                try:
                    val = (gpio_req.get_value(25) == gpiod.line.Value.ACTIVE)
                except Exception:
                    gpio_error = "Помилка читання GPIO"
                    time.sleep(0.1)
                    continue
                gpio_error = None
                pir_current_state = val
                if val and not last_val:
                    pir_count += 1
                    pir_last_time = time.strftime('%H:%M:%S')
                    try:
                        motion_history(time.time())
                    except (OSError, sqlite3.Error) as error:
                        print(f"[PIR history] {error}", flush=True)
                    if auto_alarm_enabled:
                        play_buzzer_beep(times=2, duration=0.06, interval=0.05)
                        if led_current_mode != "rainbow":
                            send_ws2813_pixels([(0, 255, 0), (0, 255, 0)])
                            time.sleep(1.5)
                            if led_current_mode == "color":
                                send_ws2813_pixels([tuple(led_color), tuple(led_color)])
                            elif led_current_mode == "off":
                                send_ws2813_pixels([(0, 0, 0), (0, 0, 0)])
                last_val = val
        except Exception as e:
            print(f"[PIR monitor] {e}", flush=True)
        time.sleep(0.1)

threading.Thread(target=pir_monitor_thread, daemon=True).start()

def get_system_status():
    host_error = None
    try:
        metrics = host_request("GET", "/status")
    except (OSError, ValueError, http.client.HTTPException):
        metrics = {"gpio": {}, "cpu_temp": None, "fan_state": None,
                   "fan_pwm": None, "fan_rpm": None, "uptime": "-", "throttled": None}
        host_error = "Host helper недоступний"
    fan_rpm = metrics["fan_rpm"]
    return {
        **metrics,
        "hardware": {
            "pir": "GPIO недоступний" if gpio_req is None else gpio_error,
            "buzzer": "GPIO недоступний" if gpio_req is None else None,
            "spi": "SPI недоступний" if spi is None else led_error,
            "tachometer": host_error or ("Тахометр недоступний" if fan_rpm is None else None),
        },
        "led_brightness": led_brightness,
        "led_error": led_error,
        "pir_state": pir_current_state,
        "pir_count": pir_count,
        "pir_history": motion_history(),
        "pir_last": pir_last_time,
        "auto_alarm": auto_alarm_enabled,
        "led_mode": led_current_mode,
        "led_color": led_color,

    }

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="uk">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Raspberry Pi 5 • Лабораторія датчиків</title>
  <style>
    :root {
      --bg: #111417;
      --card-bg: #1a1e24;
      --border: #2a313a;
      --accent-green: #00e676;
      --accent-blue: #29b6f6;
      --accent-red: #ff5252;
      --accent-yellow: #ffd600;
      --text: #e6edf3;
      --text-dim: #8b949e;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      padding: 16px;
      line-height: 1.4;
    }
    header {
      max-width: 1400px;
      margin: 0 auto 16px auto;
      display: flex;
      justify-content: space-between;
      align-items: center;
      border-bottom: 1px solid var(--border);
      padding-bottom: 12px;
      flex-wrap: wrap;
      gap: 10px;
    }
    .header-title { display: flex; align-items: center; gap: 12px; }
    .header-title h1 { font-size: 20px; font-weight: 700; color: #fff; }
    .badge {
      font-size: 11px;
      padding: 2px 8px;
      border-radius: 12px;
      font-weight: 600;
      text-transform: uppercase;
      background: rgba(0, 230, 118, 0.15);
      color: var(--accent-green);
      border: 1px solid rgba(0, 230, 118, 0.3);
    }
    .header-stats { display: flex; gap: 14px; font-size: 13px; color: var(--text-dim); }
    .header-stats span b { color: var(--text); }

    .main-grid {
      max-width: 1400px;
      margin: 0 auto;
      display: grid;
      grid-template-columns: 1fr;
      gap: 16px;
    }
    @media (min-width: 1000px) {
      .main-grid { grid-template-columns: 380px 1fr; }
    }

    .col { display: flex; flex-direction: column; gap: 16px; }

    .card {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 16px;
      box-shadow: 0 4px 12px rgba(0,0,0,0.25);
    }
    .card-head {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 14px;
      padding-bottom: 8px;
      border-bottom: 1px solid rgba(255,255,255,0.06);
    }
    .card-head h2 { font-size: 16px; font-weight: 600; display: flex; align-items: center; gap: 8px; }

    /* Controls & Buttons */
    .btn-row { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
    button {
      background: #252b33;
      color: #fff;
      border: 1px solid #3b4452;
      border-radius: 6px;
      padding: 8px 14px;
      font-size: 13px;
      font-weight: 600;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      transition: all 0.15s ease;
    }
    button:not(.pin-circle):hover { background: #323a45; border-color: #536074; transform: translateY(-1px); }
    button:active { transform: translateY(0); }
    button.btn-green { background: #1b5e20; border-color: #2e7d32; }
    button.btn-green:hover { background: #2e7d32; }
    button.btn-red { background: #b71c1c; border-color: #d32f2f; }
    button.btn-red:hover { background: #c62828; }
    button.btn-blue { background: #01579b; border-color: #0288d1; }
    button.btn-blue:hover { background: #0288d1; }
    button.btn-rainbow {
      background: linear-gradient(135deg, #e91e63, #9c27b0, #2196f3, #4caf50);
      border: none;
      box-shadow: 0 2px 8px rgba(0,0,0,0.3);
    }

    /* Motion Banner */
    .pir-banner {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 14px 18px;
      border-radius: 8px;
      background: #1e2631;
      border: 1px solid #334050;
      transition: all 0.3s ease;
    }
    .pir-banner.active {
      background: rgba(0, 230, 118, 0.15);
      border-color: var(--accent-green);
      box-shadow: 0 0 16px rgba(0, 230, 118, 0.25);
    }
    .pir-status-txt { font-size: 18px; font-weight: 700; display: flex; align-items: center; gap: 10px; }
    .pir-pulse {
      width: 14px;
      height: 14px;
      border-radius: 50%;
      background: #546e7a;
    }
    .pir-banner.active .pir-pulse {
      background: var(--accent-green);
      box-shadow: 0 0 10px var(--accent-green);
      animation: pulse 1s infinite alternate;
    }
    @keyframes pulse { from { transform: scale(0.9); } to { transform: scale(1.3); } }

    /* Switch */
    .switch-label { display: flex; align-items: center; gap: 8px; font-size: 13px; cursor: pointer; }
    .switch {
      position: relative;
      display: inline-block;
      width: 40px;
      height: 22px;
    }
    .switch input { opacity: 0; width: 0; height: 0; }
    .slider {
      position: absolute; cursor: pointer; inset: 0;
      background-color: #37474f;
      transition: .2s;
      border-radius: 22px;
    }
    .slider:before {
      position: absolute; content: "";
      height: 16px; width: 16px; left: 3px; bottom: 3px;
      background-color: white;
      transition: .2s;
      border-radius: 50%;
    }
    input:checked + .slider { background-color: var(--accent-green); }
    input:checked + .slider:before { transform: translateX(18px); }

    /* Metric Grid */
    .stat-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(110px, 1fr)); gap: 10px; margin-top: 10px; }
    .stat-box { background: #161a1f; border: 1px solid #232932; border-radius: 8px; padding: 10px; }
    .stat-label { font-size: 11px; color: var(--text-dim); text-transform: uppercase; font-weight: 600; display: block; margin-bottom: 4px; }
    .stat-val { font-size: 17px; font-weight: 700; color: #fff; font-family: ui-monospace, Consolas, monospace; }

    /* ===== TABS ===== */
    .tab-bar {
      display: flex;
      gap: 4px;
      margin-bottom: 14px;
      border-bottom: 2px solid var(--border);
      padding-bottom: 0;
    }
    .tab-btn {
      background: none;
      border: none;
      border-bottom: 3px solid transparent;
      border-radius: 0;
      color: var(--text-dim);
      font-size: 13px;
      font-weight: 600;
      padding: 8px 14px;
      cursor: pointer;
      transform: none;
      margin-bottom: -2px;
      transition: color 0.15s;
    }
    .tab-btn:hover { background: none; color: var(--text); transform: none; border-color: #536074; }
    .tab-btn.active { color: var(--accent-blue); border-color: var(--accent-blue); }
    .tab-panel { display: none; }
    .tab-panel.active { display: block; }

    /* ===== CIRCUIT DIAGRAM ===== */
    .circuit-wrap {
      display: flex;
      gap: 18px;
      align-items: flex-start;
      flex-wrap: wrap;
    }

    /* Pi 5 Box */
    .pi5-box {
      background: #1c2b1c;
      border: 2px solid #3c5435;
      border-radius: 10px;
      padding: 10px 8px;
      min-width: 160px;
      flex-shrink: 0;
    }
    .pi5-box-title {
      font-size: 11px;
      font-weight: 700;
      color: #7cb342;
      text-align: center;
      margin-bottom: 8px;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }
    .pi5-pin-row {
      display: flex;
      align-items: center;
      gap: 6px;
      margin-bottom: 5px;
      font-size: 11px;
    }
    .pi5-rtc-row {
      border-top: 1px solid #3c5435;
      margin-top: 8px;
      padding-top: 8px;
    }
    .pi5-pin-num {
      width: 24px;
      height: 18px;
      background: #2e3b2e;
      border: 1px solid #4c6e44;
      border-radius: 3px;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 9px;
      font-weight: 700;
      color: #a5d6a7;
      flex-shrink: 0;
    }
    .pi5-wire {
      height: 3px;
      width: 32px;
      border-radius: 2px;
      flex-shrink: 0;
    }
    .wire-red { background: #ef5350; }
    .wire-black { background: #546e7a; }
    .wire-green { background: #66bb6a; }
    .wire-yellow { background: #ffd600; }
    .wire-blue { background: #42a5f5; }
    .wire-orange { background: #ff9800; }
    .wire-purple { background: #ab47bc; }
    .pi5-pin-label { font-size: 10px; color: #ccc; white-space: nowrap; }

    /* Connector arrow */
    .circuit-arrow {
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 22px;
      color: #536074;
      flex-shrink: 0;
      align-self: center;
    }

    /* Breadboard */
    .breadboard-wrap {
      flex: 1;
      min-width: 280px;
    }
    .breadboard {
      background: #1a2535;
      border: 2px solid #2d4060;
      border-radius: 10px;
      padding: 10px;
      position: relative;
    }
    .bb-title {
      font-size: 11px;
      font-weight: 700;
      color: #42a5f5;
      text-align: center;
      margin-bottom: 8px;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }
    .bb-rail {
      display: flex;
      align-items: center;
      gap: 4px;
      margin-bottom: 5px;
      padding: 5px 8px;
      border-radius: 5px;
    }
    .bb-rail.rail-5v {
      background: rgba(239,83,80,0.15);
      border: 1px solid rgba(239,83,80,0.4);
    }
    .bb-rail.rail-gnd {
      background: rgba(84,110,122,0.15);
      border: 1px solid rgba(84,110,122,0.4);
    }
    .bb-rail-label { font-size: 11px; font-weight: 700; min-width: 32px; }
    .rail-5v .bb-rail-label { color: #ef5350; }
    .rail-gnd .bb-rail-label { color: #90a4ae; }
    .bb-hole {
      width: 8px; height: 8px;
      border-radius: 50%;
      border: 1.5px solid rgba(255,255,255,0.15);
    }
    .rail-5v .bb-hole { background: #c62828; }
    .rail-gnd .bb-hole { background: #37474f; }
    .bb-rail-desc { font-size: 10px; color: var(--text-dim); margin-left: 4px; }

    /* Module cards on breadboard */
    .bb-modules {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 7px;
      margin-top: 10px;
    }
    .bb-module {
      background: #12191f;
      border: 1px solid #2a3545;
      border-radius: 7px;
      padding: 8px;
      transition: all 0.3s ease;
    }
    .bb-module.pir-highlight {
      border-color: var(--accent-yellow);
      background: rgba(255, 214, 0, 0.08);
      box-shadow: 0 0 12px rgba(255,214,0,0.3);
    }
    .bb-mod-title {
      font-size: 10px;
      font-weight: 700;
      color: #90caf9;
      margin-bottom: 5px;
      display: flex;
      align-items: center;
      gap: 4px;
    }
    .bb-mod-row {
      display: flex;
      align-items: center;
      gap: 4px;
      font-size: 9.5px;
      color: var(--text-dim);
      margin-bottom: 2px;
    }
    .bb-mod-dot {
      width: 7px; height: 7px;
      border-radius: 50%;
      flex-shrink: 0;
    }
    .dot-red { background: #ef5350; }
    .dot-black { background: #546e7a; }
    .dot-green { background: #66bb6a; }
    .dot-yellow { background: #ffd600; }
    .dot-blue { background: #42a5f5; }
    .dot-orange { background: #ff9800; }
    .dot-purple { background: #ab47bc; }
    .dot-white { background: #cfd8dc; }

    /* Virtual WS2813 LEDs */
    .vled-row {
      display: flex;
      gap: 6px;
      margin-top: 5px;
    }
    .vled {
      width: 18px; height: 18px;
      border-radius: 50%;
      border: 2px solid rgba(255,255,255,0.15);
      background: #1a1a1a;
      transition: background 0.3s, box-shadow 0.3s;
    }
    .vled.on {
      box-shadow: 0 0 8px var(--vled-color, #fff);
    }

    /* YwRobot Module */
    .ywrobot-card {
      grid-column: 1 / -1;
      background: #1a1424;
      border: 1px solid #3c2d6b;
      border-radius: 7px;
      padding: 8px;
    }
    .ywrobot-title {
      font-size: 10px;
      font-weight: 700;
      color: #ce93d8;
      margin-bottom: 6px;
      display: flex;
      align-items: center;
      gap: 4px;
    }
    .ywrobot-rails {
      display: flex;
      gap: 8px;
    }
    .ywrobot-rail {
      flex: 1;
      display: flex;
      align-items: center;
      gap: 4px;
      font-size: 9.5px;
      padding: 3px 6px;
      border-radius: 4px;
    }
    .ywrobot-rail.r5v { background: rgba(239,83,80,0.12); border: 1px solid rgba(239,83,80,0.3); color: #ef9a9a; }
    .ywrobot-rail.rgnd { background: rgba(84,110,122,0.12); border: 1px solid rgba(84,110,122,0.3); color: #90a4ae; }

    /* Pinout Widget Styles */
    .pinout-container{background:#4d6a45;border-radius:8px;padding:12px;margin-top:14px;color:#fff;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;box-shadow:inset 0 0 10px rgba(0,0,0,0.5)}
    .pinout-header{display:flex;align-items:center;gap:10px;margin-bottom:12px;padding-bottom:8px;border-bottom:1px solid rgba(255,255,255,0.2)}
    .pinout-logo{display:grid;grid-template-columns:repeat(3, 7px);gap:2px}
    .pinout-logo span{width:7px;height:7px;border-radius:1px}
    .pinout-title{font-size:15px;font-weight:700;line-height:1.2;color:#fff}
    .pinout-subtitle{font-size:10px;color:rgba(255,255,255,0.7);font-weight:400}
    .pinout-board{display:flex;flex-direction:column;gap:3px}
    .pin-row{display:grid;grid-template-columns:1fr 24px 24px 1fr;gap:4px;align-items:center}
    .pin-label{font-size:11px;font-weight:500;padding:2px 6px;border-radius:4px;display:flex;align-items:center;gap:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;transition:background .2s}
    .pin-label.left{justify-content:flex-end;text-align:right}
    .pin-label.right{justify-content:flex-start;text-align:left}
    .pin-sub{font-size:9.5px;opacity:0.8;font-weight:normal}
    .pin-circle{width:20px;height:20px;padding:0;justify-self:center;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:9px;font-weight:bold;color:#fff;cursor:pointer;position:relative;border:1.5px solid rgba(0,0,0,0.4);transition:transform 0.15s, box-shadow 0.15s}
    .pin-circle:hover{transform:scale(1.3);z-index:10;box-shadow:0 0 8px #fff}
    .pin-circle .pin-dot{width:6px;height:6px;border-radius:50%;background:#fff}
    .pin-circle.rect{border-radius:3px}
    /* Pin Color Categories */
    .p-pwr5{background:#e53935}
    .p-pwr3{background:#fb8c00}
    .p-gnd{background:#212121;border-color:#111}
    .p-gpio{background:#7cb342}
    .p-i2c{background:#039be5}
    .p-uart{background:#5e35b1}
    .p-spi{background:#d81b60}
    .p-pcm{background:#00897b}
    .pin-tag{font-size:8px;padding:1px 4px;border-radius:3px;background:rgba(0,0,0,0.4);margin-left:4px;font-weight:600;letter-spacing:0.02em}
    .pin-tag.hi{background:#4caf50;color:#fff}
    .pin-tag.lo{background:#37474f;color:#b0bec5}
    .pin-tag.active-dev{background:#ffeb3b;color:#000;font-weight:bold;box-shadow:0 0 6px #ffeb3b}
    #pin-info-bar{margin-top:10px;background:rgba(0,0,0,0.4);border-radius:6px;padding:8px 10px;font-size:11.5px;min-height:22px;display:flex;align-items:center;justify-content:space-between;color:#e0e0e0}

    .pin-circle:focus-visible{outline:2px solid #fff;outline-offset:2px}
    #pin-info-bar{flex-wrap:wrap;gap:6px}
    #pin-legend{flex-wrap:wrap}
    .wiring-heading{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:18px;flex-wrap:wrap}
    .wiring-heading h2{font-size:18px;margin:0}.wiring-heading p{font-size:12px;color:var(--text-dim);margin:8px 0 0}
    .wiring-heading label{font-size:12px;white-space:nowrap;cursor:pointer}
    .circuit-wrap{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:16px}
    .pinout-container{margin-top:0;min-width:0;background:#21382d;border:1px solid #42624e;box-shadow:none}
    .breadboard-wrap{min-width:0;width:100%}.bb-modules{grid-template-columns:1fr}
    .pin-row{grid-template-columns:minmax(0,1fr) 24px 24px minmax(0,1fr);margin:8px 0}
    .pin-label{flex-wrap:wrap;white-space:normal;font-size:10px;padding:2px}.pin-sub{display:none}
    .pin-tag.active-dev{background:#294b3b;color:#b7e9c7;box-shadow:none;font-size:9px}
    .pin-circle{opacity:.6}.pin-circle[data-pin="12"],.pin-circle[data-pin="16"],.pin-circle[data-pin="18"],.pin-circle[data-pin="19"],.pin-circle[data-pin="20"],.pin-circle[data-pin="22"]{opacity:1}
    .bb-module{cursor:pointer;transition:border-color .2s,background .2s}
    .bb-module:focus-visible{outline:2px solid #65d9b4;outline-offset:3px}
    .bb-module.connection-selected{border:1px solid #65d9b4;background:#203b34;box-shadow:0 0 0 2px #65d9b422}
    .pin-circle.connection-selected{box-shadow:0 0 0 3px #65d9b4;opacity:1}
    @media(max-width:1199px){.circuit-wrap{grid-template-columns:1fr}.bb-modules{grid-template-columns:repeat(2,minmax(0,1fr))}}
    @media(max-width:480px){.bb-modules{grid-template-columns:1fr}.header-stats{flex-wrap:wrap}.pin-label{font-size:10px}}
    .motion-history{margin-top:16px;border-top:1px solid #303640;padding-top:12px}
    .motion-history summary{cursor:pointer;font-size:13px;font-weight:600}.motion-history summary span{color:var(--text-dim);font-weight:400;font-size:11px;margin-left:6px}
    #pir-history{max-height:170px;overflow:auto;list-style:none;padding:0;margin:10px 0 0;font-size:12px;font-variant-numeric:tabular-nums}
    #pir-history li{display:flex;justify-content:space-between;gap:10px;padding:7px 0;border-bottom:1px solid #252b33;color:var(--text-dim)}
    #pir-history li span{color:var(--text)}
    .led-overview{display:flex;align-items:center;gap:14px;margin:16px 0}.led-overview p{font-size:12px;color:var(--text-dim);margin:6px 0}
    .led-preview{width:42px;height:42px;border-radius:12px;background:#15191f;border:1px solid #414957;flex-shrink:0}
    .led-preview.rainbow{background:conic-gradient(#d96868,#d7bb6b,#72b68b,#6e9bd1,#b480c2,#d96868)}
    .led-presets{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}
    .led-presets button{background:#242a33;color:#e0e5ed;border:1px solid #3b4350;box-shadow:none;font-weight:500;min-height:38px}
    .led-presets button:hover{background:#303844}.led-presets button[aria-pressed="true"]{background:#3b4655;border-color:#a9b7ca;color:#fff}
    .fan-power{display:flex;justify-content:space-between;gap:10px;padding:12px;border:1px solid #354238;border-radius:8px;background:#1b2822;font-size:13px}
    .power-note{font-size:11px;color:var(--text-dim);margin:8px 0 0}
    .history-tools{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-top:12px;font-size:12px}
    .history-tools select{background:#242a33;color:#e0e5ed;border:1px solid #414957;border-radius:6px;padding:6px}
    #motion-chart{display:flex;align-items:flex-end;gap:3px;height:65px;margin:10px 0;border-bottom:1px solid #414957}
    .chart-bar{flex:1;background:#303943;border-radius:3px 3px 0 0}.chart-bar.active{background:#79aa99}
    .data-stale .stat-val,.data-stale .pir-banner,.data-stale .pinout-board,.data-stale .led-overview,.data-stale .fan-power{opacity:.4}
    .data-stale #connection-status{background:#493030;border-color:#925959;color:#f1baba}
    .module-select{width:100%;text-align:left;background:none;border:0;margin:0;padding:3px 0;box-shadow:none;color:var(--text);font-size:12px}
    .module-details{font-size:11px;margin-top:8px;color:var(--text-dim)}.module-details summary{cursor:pointer;margin-bottom:8px}
    .pin-tag{white-space:normal}
    .brightness-control{display:flex;justify-content:space-between;font-size:12px;margin:12px 0 8px;color:var(--text-dim)}
    #led-brightness{width:100%;accent-color:#a9b7ca;margin-bottom:12px}
    #led-control-status{color:#e7aeae;font-size:12px;margin:6px 0}
  </style>
</head>
<body>

  <header>
    <div class="header-title">
      <h1>Raspberry Pi 5 • Лабораторія</h1>
      <span class="badge" id="connection-status" role="status">Оновлення…</span>
    </div>

  </header>

  <div class="main-grid">
    <!-- Ліва колонка: Керування датчиками та пристроями -->
    <div class="col">

      <div id="control-status" role="status"></div>

      <!-- Датчик руху HC-SR505 -->
      <div class="card">
        <div class="card-head">
          <h2>Датчик руху HC-SR505</h2>
          <label class="switch-label">
            <span>Авто-тривога</span>
            <label class="switch">
              <input type="checkbox" id="chk-auto-alarm" onchange="toggleAutoAlarm(this.checked)">
              <span class="slider"></span>
            </label>
          </label>
        </div>

        <div class="pir-banner" id="pir-banner">
          <div class="pir-status-txt">
            <div class="pir-pulse"></div>
            <span id="pir-text">Опитування...</span>
          </div>
          <span style="font-size:12px; color:var(--text-dim);">Pin 22 (GPIO 25)</span>
        </div>

        <div class="stat-grid">
          <div class="stat-box">
            <span class="stat-label">За цей запуск</span>
            <span class="stat-val" id="stat-pir-count">0</span>
          </div>
          <div class="stat-box">
            <span class="stat-label">Останній рух</span>
            <span class="stat-val" id="stat-pir-last" style="font-size:14px">-</span>
          </div>
        </div>
        <details class="motion-history" open>
          <summary>Історія спрацювань</summary>
          <div class="history-tools"><label>Період <select id="history-period" onchange="loadMotionHistory()"><option value="hour">Остання година</option><option value="today">Сьогодні</option></select></label><span id="history-count"></span></div>
          <div id="motion-chart" role="img" aria-label="Активність руху"></div>
          <p class="power-note">Збережені події · останні 20 у списку · архів до 1000</p>
          <ol id="pir-history"><li>Завантаження історії…</li></ol>
        </details>
      </div>

      <div class="card led-card">
        <div class="card-head"><h2>Адресні світлодіоди WS2813-2</h2></div>
        <p class="power-note" id="led-hardware-status"></p>
        <div class="led-overview"><div class="led-preview" id="led-preview"></div>
          <div><strong id="led-mode-label">Вимкнено</strong><p>2 світлодіоди · SPI / Pin 19</p></div></div>
        <label class="brightness-control" for="led-brightness">Яскравість <output id="brightness-value">100%</output></label>
        <input type="range" id="led-brightness" min="0" max="100" value="100" oninput="document.getElementById('brightness-value').textContent=this.value+'%'" onchange="setBrightness(Number(this.value))">
        <div id="led-control-status" role="status"></div>
        <div class="led-presets" aria-label="Режими світлодіодів">
          <button data-led-mode="rainbow" onclick="setLedMode('rainbow')">Веселка</button>
          <button data-led-color="0,255,0" onclick="setLedColor(0,255,0)">Зелений</button>
          <button data-led-color="0,120,255" onclick="setLedColor(0,120,255)">Синій</button>
          <button data-led-color="255,0,0" onclick="setLedColor(255,0,0)">Червоний</button>
          <button data-led-color="255,180,0" onclick="setLedColor(255,180,0)">Жовтий</button>
          <button data-led-color="200,0,255" onclick="setLedColor(200,0,255)">Фіолетовий</button>
          <button data-led-color="200,200,200" onclick="setLedColor(200,200,200)">Білий</button>
          <button data-led-mode="off" onclick="setLedMode('off')">Вимкнути</button>
        </div>
      </div>

      <!-- Зумер MH-FMD -->
      <div class="card">
        <div class="card-head">
          <h2>Зумер (MH-FMD Buzzer)</h2>
          <span style="font-size:12px; color:var(--text-dim)">Pin 18 (GPIO 24)</span>
        </div>
        <p class="power-note" id="buzzer-hardware-status"></p>
        <div class="btn-row">
          <button class="btn-green" onclick="triggerBuzzer('beep')">🔔 Короткий сигнал</button>
          <button class="btn-red" onclick="triggerBuzzer('alarm')">🚨 Сирена тривоги</button>
        </div>
      </div>

      <!-- Вентилятор SC1148 Active Cooler -->
      <div class="card">
        <div class="card-head">
          <h2>Active Cooler SC1148</h2>
          <span style="font-size:12px; color:var(--text-dim)">ШІМ Pin 12 (GPIO 18)</span>
        </div>
        <div class="fan-power"><span>Стан кулера</span><strong id="fan-power-state">Невідомо</strong></div>
        <p class="power-note">За тахометром і ШІМ · напруга живлення не вимірюється</p>
        <div class="stat-grid">
          <div class="stat-box">
            <span class="stat-label">Температура CPU</span>
            <span class="stat-val" id="fan-temp">-</span>
          </div>
          <div class="stat-box">
            <span class="stat-label">Рівень кулера</span>
            <span class="stat-val" id="fan-state">-</span>
          </div>
          <div class="stat-box">
            <span class="stat-label">ШІМ (0-255)</span>
            <span class="stat-val" id="fan-pwm">-</span>
          </div>
          <div class="stat-box">
            <span class="stat-label">Оберти (RPM)</span>
            <span class="stat-val" id="fan-rpm">-</span>
          </div>
        </div>
        <div class="btn-row">
          <button id="fan-auto" onclick="setFan('auto')" disabled>Авто</button>
          <button id="fan-on" class="btn-green" onclick="setFan(true)" disabled>Максимальні оберти</button>
          <button id="fan-off" class="btn-red" onclick="setFan(false)" disabled>Вимкнути кулер</button>
        </div>
        <p class="power-note">Авто — крива температури ОС. Ручні команди тимчасові: тепловий захист залишається активним.</p>
        <div id="fan-control-status" role="status"></div>
      </div>

    </div>

    <!-- Права колонка: Схема підключення + 40-Pin Pinout -->
    <div class="col">
      <div class="card" style="padding: 14px;">

        <div class="wiring-heading"><div><h2>Підключення та GPIO</h2>
          <p>Оберіть модуль або пін — побачите пов’язані з’єднання</p></div>
          <label><input type="checkbox" id="show-all-pins" onchange="renderPinout()"> Усі 40 пінів</label></div>
        <div class="wiring-panel">
          <div class="circuit-wrap">

          <div class="pinout-container">
            <div class="pinout-header">
              <div class="pinout-logo">
                <span style="background:#00e676"></span><span style="background:#00e676"></span><span style="background:#00e676"></span>
                <span style="background:#d50000"></span><span style="background:#d50000"></span><span style="background:#d50000"></span>
                <span style="background:#d50000"></span><span style="background:#d50000"></span><span style="background:#d50000"></span>
              </div>
              <div>
                <div class="pinout-title">Raspberry Pi 5</div>
                <div class="pinout-subtitle">Фізичний пін · BCM GPIO · стан</div>
              </div>
            </div>

            <div class="pinout-board" id="pinout-board">
              <!-- Rendered by JavaScript -->
            </div>

            <div id="pin-info-bar">
              <span id="pin-info-text">💡 Наведіть курсор або натисніть на пін для перегляду деталей</span>
              <span id="pin-legend" style="font-size:10px;opacity:0.8;display:flex;gap:6px">
                <span style="color:#ff8a80">● 5V</span>
                <span style="color:#ffb74d">● 3.3V</span>
                <span style="color:#81c784">● GPIO</span>
                <span style="color:#90caf9">● I2C/UART/PCM</span>
              </span>
            </div>
          </div>

            <!-- Breadboard + Modules -->
            <div class="breadboard-wrap">
              <div class="breadboard">
                <div class="bb-title">MB-102 Breadboard</div>

                <!-- YwRobot Power Module (spans full width) -->
                <div class="ywrobot-card">
                  <div class="ywrobot-title">
                    YwRobot 545043 Power Module
                  </div>
                  <div class="ywrobot-rails">
                    <div class="ywrobot-rail r5v">
                      <div class="bb-mod-dot dot-red"></div>
                      +5V вихід → червона рейка макетки
                    </div>
                    <div class="ywrobot-rail rgnd">
                      <div class="bb-mod-dot dot-black"></div>
                      GND → синя рейка (земля)
                    </div>
                  </div>
                </div>

                <!-- Power Rails -->
                <div style="margin-top:8px;">
                  <div class="bb-rail rail-5v">
                    <span class="bb-rail-label">+5V</span>
                    <div class="bb-hole"></div><div class="bb-hole"></div><div class="bb-hole"></div>
                    <div class="bb-hole"></div><div class="bb-hole"></div><div class="bb-hole"></div>
                    <div class="bb-hole"></div><div class="bb-hole"></div><div class="bb-hole"></div>
                    <span class="bb-rail-desc">Живить: SC1148, WS2813, HC-SR505</span>
                  </div>
                  <div class="bb-rail rail-gnd">
                    <span class="bb-rail-label">GND</span>
                    <div class="bb-hole"></div><div class="bb-hole"></div><div class="bb-hole"></div>
                    <div class="bb-hole"></div><div class="bb-hole"></div><div class="bb-hole"></div>
                    <div class="bb-hole"></div><div class="bb-hole"></div><div class="bb-hole"></div>
                    <span class="bb-rail-desc">Спільна земля з Pi 5 • пін 20</span>
                  </div>
                </div>

                <!-- Module Cards -->
                <div class="bb-modules">

                  <!-- SC1148 Active Cooler -->
                  <div class="bb-module" data-device="fan">
                    <button class="bb-mod-title module-select" type="button" onclick="selectModule('fan')">SC1148 Active Cooler</button>
                    <details class="module-details"><summary>Живлення та підключення</summary>
                    <div class="bb-mod-row">
                      <div class="bb-mod-dot dot-red"></div>
                      <span>+5V ← рейка +5V</span>
                    </div>
                    <div class="bb-mod-row">
                      <div class="bb-mod-dot dot-black"></div>
                      <span>GND ← рейка GND</span>
                    </div>
                    <div class="bb-mod-row">
                      <div class="bb-mod-dot dot-orange"></div>
                      <span>PWM ← Pin 12 (GPIO 18)</span>
                    </div>
                    <div class="bb-mod-row">
                      <div class="bb-mod-dot dot-yellow"></div>
                      <span>Tach → пін 16 (GPIO 23)</span>
                    </div>
                    </details>
                  </div>

                  <!-- MH-FMD Buzzer -->
                  <div class="bb-module" data-device="buzzer">
                    <button class="bb-mod-title module-select" type="button" onclick="selectModule('buzzer')">MH-FMD Зумер</button>
                    <details class="module-details"><summary>Живлення та підключення</summary>
                    <div class="bb-mod-row">
                      <div class="bb-mod-dot dot-red"></div>
                      <span>+ ← рейка +5V</span>
                    </div>
                    <div class="bb-mod-row">
                      <div class="bb-mod-dot dot-black"></div>
                      <span>GND ← рейка GND</span>
                    </div>
                    <div class="bb-mod-row">
                      <div class="bb-mod-dot dot-blue"></div>
                      <span>I/O ← Pin 18 (GPIO 24)</span>
                    </div>
                    </details>
                  </div>

                  <!-- WS2813-2 LEDs -->
                  <div class="bb-module" data-device="led">
                    <button class="bb-mod-title module-select" type="button" onclick="selectModule('led')">WS2813-2 LED × 2</button>
                    <details class="module-details"><summary>Живлення та підключення</summary>
                    <div class="bb-mod-row">
                      <div class="bb-mod-dot dot-red"></div>
                      <span>+5V ← рейка +5V</span>
                    </div>
                    <div class="bb-mod-row">
                      <div class="bb-mod-dot dot-black"></div>
                      <span>GND ← рейка GND</span>
                    </div>
                    <div class="bb-mod-row">
                      <div class="bb-mod-dot dot-purple"></div>
                      <span>DIN ← Pin 19 (SPI MOSI)</span>
                    </div>
                    <div class="vled-row">
                      <div class="vled" id="vled-0" title="LED 1"></div>
                      <div class="vled" id="vled-1" title="LED 2"></div>
                    </div>
                    </details>
                  </div>

                  <!-- HC-SR505 PIR -->
                  <div class="bb-module" data-device="pir" id="pir-module-card">
                    <button class="bb-mod-title module-select" type="button" onclick="selectModule('pir')">HC-SR505 PIR</button>
                    <details class="module-details"><summary>Живлення та підключення</summary>
                    <div class="bb-mod-row">
                      <div class="bb-mod-dot dot-red"></div>
                      <span>+ ← рейка +5V</span>
                    </div>
                    <div class="bb-mod-row">
                      <div class="bb-mod-dot dot-black"></div>
                      <span>GND ← рейка GND (середній контакт)</span>
                    </div>
                    <div class="bb-mod-row">
                      <div class="bb-mod-dot dot-green"></div>
                      <span>OUT → Pin 22 (GPIO 25)</span>
                    </div>
                    <div style="margin-top:5px; font-size:9px; color:var(--text-dim);" id="pir-module-status">
                      ⬤ Очікування...
                    </div>
                    </details>
                  </div>

                </div><!-- /bb-modules -->
              </div><!-- /breadboard -->
              <p style="font-size:12px;color:var(--text-dim)">🔋 RTC батарейка → окремий роз’єм J5 / BAT</p>
            </div><!-- /breadboard-wrap -->
          </div><!-- /circuit-wrap -->
        </div><!-- /wiring-panel -->



      </div><!-- /card -->
    </div><!-- /right col -->
  </div><!-- /main-grid -->

  <script>
    let pinoutRendered = false;

    // ===== PINOUT =====
    const PINOUT_DEFS = [
      // Pair 1: Pin 1 & Pin 2
      { pin: 1, name: "3v3 Power", sub: "3.3V DC", type: "pwr3", isPwr: true },
      { pin: 2, name: "5v Power", sub: "5V DC", type: "pwr5", isPwr: true },
      // Pair 2: Pin 3 & Pin 4
      { pin: 3, name: "GPIO 2", sub: "SDA / I2C1", type: "i2c", gpio: 2 },
      { pin: 4, name: "5v Power", sub: "5V DC", type: "pwr5", isPwr: true },
      // Pair 3: Pin 5 & Pin 6
      { pin: 5, name: "GPIO 3", sub: "SCL / I2C1", type: "i2c", gpio: 3 },
      { pin: 6, name: "Ground", sub: "0V GND", type: "gnd", isGnd: true },
      // Pair 4: Pin 7 & Pin 8
      { pin: 7, name: "GPIO 4", sub: "GPCLK0", type: "gpio", gpio: 4 },
      { pin: 8, name: "GPIO 14", sub: "TXD / UART", type: "uart", gpio: 14 },
      // Pair 5: Pin 9 & Pin 10
      { pin: 9, name: "Ground", sub: "0V GND", type: "gnd", isGnd: true },
      { pin: 10, name: "GPIO 15", sub: "RXD / UART", type: "uart", gpio: 15 },
      // Pair 6: Pin 11 & Pin 12
      { pin: 11, name: "GPIO 17", sub: "General IO", type: "gpio", gpio: 17 },
      { pin: 12, name: "GPIO 18", sub: "PWM0_CHAN2", type: "pcm", gpio: 18, device: "🌀 Кулер PWM" },
      // Pair 7: Pin 13 & Pin 14
      { pin: 13, name: "GPIO 27", sub: "General IO", type: "gpio", gpio: 27 },
      { pin: 14, name: "Ground", sub: "0V GND", type: "gnd", isGnd: true },
      // Pair 8: Pin 15 & Pin 16
      { pin: 15, name: "GPIO 22", sub: "General IO", type: "gpio", gpio: 22 },
      { pin: 16, name: "GPIO 23", sub: "Вхід тахометра", type: "gpio", gpio: 23, device: "🌀 Кулер Tach" },
      // Pair 9: Pin 17 & Pin 18
      { pin: 17, name: "3v3 Power", sub: "3.3V DC", type: "pwr3", isPwr: true },
      { pin: 18, name: "GPIO 24", sub: "Вихід сигналу", type: "gpio", gpio: 24, device: "🔔 Зумер I/O" },
      // Pair 10: Pin 19 & Pin 20
      { pin: 19, name: "GPIO 10", sub: "MOSI / SPI0", type: "spi", gpio: 10, device: "💡 WS2813 DIN" },
      { pin: 20, name: "Ground", sub: "0V GND", type: "gnd", isGnd: true, device: "⏚ GND макетки" },
      // Pair 11: Pin 21 & Pin 22
      { pin: 21, name: "GPIO 9", sub: "MISO / SPI0", type: "spi", gpio: 9 },
      { pin: 22, name: "GPIO 25", sub: "Вхід", type: "gpio", gpio: 25, device: "👁️ PIR OUT" },
      // Pair 12: Pin 23 & Pin 24
      { pin: 23, name: "GPIO 11", sub: "SCLK / SPI0", type: "spi", gpio: 11 },
      { pin: 24, name: "GPIO 8", sub: "CE0 / SPI0", type: "spi", gpio: 8 },
      // Pair 13: Pin 25 & Pin 26
      { pin: 25, name: "Ground", sub: "0V GND", type: "gnd", isGnd: true },
      { pin: 26, name: "GPIO 7", sub: "CE1 / SPI0", type: "spi", gpio: 7 },
      // Pair 14: Pin 27 & Pin 28
      { pin: 27, name: "GPIO 0", sub: "ID_SD (EEPROM)", type: "i2c", gpio: 0 },
      { pin: 28, name: "GPIO 1", sub: "ID_SC (EEPROM)", type: "i2c", gpio: 1 },
      // Pair 15: Pin 29 & Pin 30
      { pin: 29, name: "GPIO 5", sub: "General IO", type: "gpio", gpio: 5 },
      { pin: 30, name: "Ground", sub: "0V GND", type: "gnd", isGnd: true },
      // Pair 16: Pin 31 & Pin 32
      { pin: 31, name: "GPIO 6", sub: "General IO", type: "gpio", gpio: 6 },
      { pin: 32, name: "GPIO 12", sub: "PWM0", type: "pcm", gpio: 12 },
      // Pair 17: Pin 33 & Pin 34
      { pin: 33, name: "GPIO 13", sub: "PWM1", type: "pcm", gpio: 13 },
      { pin: 34, name: "Ground", sub: "0V GND", type: "gnd", isGnd: true },
      // Pair 18: Pin 35 & Pin 36
      { pin: 35, name: "GPIO 19", sub: "PCM FS / MISO", type: "pcm", gpio: 19 },
      { pin: 36, name: "GPIO 16", sub: "General IO", type: "gpio", gpio: 16 },
      // Pair 19: Pin 37 & Pin 38
      { pin: 37, name: "GPIO 26", sub: "General IO", type: "gpio", gpio: 26 },
      { pin: 38, name: "GPIO 20", sub: "PCM DIN / MOSI", type: "pcm", gpio: 20 },
      // Pair 20: Pin 39 & Pin 40
      { pin: 39, name: "Ground", sub: "0V GND", type: "gnd", isGnd: true },
      { pin: 40, name: "GPIO 21", sub: "PCM DOUT / SCLK", type: "pcm", gpio: 21 }
    ];

    let gpioState = {};
    let selectedPin;

    const MODULE_PINS = {fan: [12,16], buzzer: [18], led: [19], pir: [22]};
    function highlightConnections(pins) {
      document.querySelectorAll('[data-pin]').forEach(el => el.classList.toggle('connection-selected', pins.includes(Number(el.dataset.pin))));
      document.querySelectorAll('[data-device]').forEach(el => {
        const active = MODULE_PINS[el.dataset.device].some(pin => pins.includes(pin));
        el.classList.toggle('connection-selected', active);
        el.querySelector('.module-select').setAttribute('aria-pressed', String(active));
      });
    }
    function selectModule(key) {
      showPinInfo(MODULE_PINS[key][0]);
      highlightConnections(MODULE_PINS[key]);
    }
    function renderPinout() {
      const board = document.getElementById('pinout-board');
      let html = '';
      for (let i = 0; i < PINOUT_DEFS.length; i += 2) {
        const left = PINOUT_DEFS[i];
        const right = PINOUT_DEFS[i + 1];
        if (!document.getElementById('show-all-pins').checked && !left.device && !right.device) continue;
        html += `
          <div class="pin-row">
            <div class="pin-label left">
              <span class="pin-name">${left.name}</span>
              ${left.sub ? `<span class="pin-sub">(${left.sub})</span>` : ''}
              <span id="pin-tag-${left.pin}"></span>
            </div>
            ${renderPinButton(left)}
            ${renderPinButton(right)}
            <div class="pin-label right">
              <span id="pin-tag-${right.pin}"></span>
              <span class="pin-name">${right.name}</span>
              ${right.sub ? `<span class="pin-sub">(${right.sub})</span>` : ''}
            </div>
          </div>
        `;
      }
      board.innerHTML = html;
      pinoutRendered = true;
      updatePinoutState(gpioState);
    }

    function renderPinButton(pin) {
      return `<button type="button" data-pin="${pin.pin}" class="pin-circle p-${pin.type} ${pin.pin === 1 ? 'rect' : ''}"
        aria-label="Pin ${pin.pin} ${pin.name}" onmouseover="showPinInfo(${pin.pin})"
        onfocus="showPinInfo(${pin.pin})" onclick="showPinInfo(${pin.pin})">
        ${pin.pin}
      </button>`;
    }

    function showPinInfo(pinNum, highlight = true) {
      selectedPin = pinNum;
      if (highlight) highlightConnections([pinNum]);
      const pin = PINOUT_DEFS[pinNum - 1];
      const state = gpioState[pin.gpio];
      let info = `<b>Pin ${pin.pin}</b>`;
      if (pin.name) info += `: ${pin.name} (${pin.sub})`;
      if (pin.device) info += ` • <span style="color:#ffeb3b">Підключено за схемою: ${pin.device}</span>`;
      if (state) {
        const level = { hi: 'Високий (1)', lo: 'Низький (0)' }[state.level] || 'Невідомо';
        info += ` • Режим: <b>${gpioModeLabel(state.mode)}</b> • Рівень: <b>${level}</b>`;
      } else if (pin.isPwr) {
        info += ' • Живлення';
      } else if (pin.isGnd) {
        info += ' • Заземлення (GND)';
      }
      document.getElementById('pin-info-text').innerHTML = info;
    }

    function gpioModeLabel(mode) {
      const labels = {ip: 'Вхід', op: 'Вихід', no: 'Не налаштовано'};
      return labels[mode] || (/^a[0-9]+$/i.test(mode) ? 'Альт. функція ' + mode.slice(1) : 'Невідомо');
    }
    function updatePinoutState(states) {
      gpioState = states;
      if (!pinoutRendered) return;

      for (const pin of PINOUT_DEFS) {
        const tag = document.getElementById(`pin-tag-${pin.pin}`);
        if (!tag) continue;
        const state = gpioState[pin.gpio];
        if (pin.device) {
          tag.className = 'pin-tag active-dev';
          tag.textContent = pin.device;
        } else if (state) {
          tag.className = 'pin-tag ' + (state.level === 'hi' ? 'hi' : 'lo');
          tag.textContent = `${gpioModeLabel(state.mode)}:${({hi: '1', lo: '0'})[state.level] || '—'}`;
        } else {
          tag.className = '';
          tag.textContent = '';
        }
      }
      if (selectedPin !== undefined) showPinInfo(selectedPin, false);
    }

    renderPinout();

    // ===== VIRTUAL LEDS =====
    let _rainbowStep = 0;
    let _rainbowTimer = null;
    function rainbowWheel(pos) {
      pos = 255 - (pos & 255);
      if (pos < 85)  return [255 - pos*3, 0, pos*3];
      if (pos < 170) { pos -= 85; return [0, pos*3, 255 - pos*3]; }
      pos -= 170; return [pos*3, 255 - pos*3, 0];
    }
    function setVled(idx, r, g, b) {
      const el = document.getElementById('vled-' + idx);
      if (!el) return;
      if (r === 0 && g === 0 && b === 0) {
        el.style.background = '#1a1a1a';
        el.style.setProperty('--vled-color', 'transparent');
        el.classList.remove('on');
      } else {
        const hex = '#' + [r,g,b].map(v => v.toString(16).padStart(2,'0')).join('');
        el.style.background = hex;
        el.style.setProperty('--vled-color', hex);
        el.classList.add('on');
      }
    }
    function startVledRainbow() {
      if (_rainbowTimer) return;
      _rainbowTimer = setInterval(() => {
        const c1 = rainbowWheel(_rainbowStep);
        const c2 = rainbowWheel(_rainbowStep + 128);
        setVled(0, ...c1);
        setVled(1, ...c2);
        _rainbowStep = (_rainbowStep + 4) % 256;
      }, 60);
    }
    function stopVledRainbow() {
      if (_rainbowTimer) { clearInterval(_rainbowTimer); _rainbowTimer = null; }
    }

    // ===== STATUS POLL =====
    let statusBusy = false;
    let lastHistoryKey, lastHistoryFetch = 0, historyRequest = 0;
    async function loadMotionHistory() {
      const request = ++historyRequest;
      const period = document.getElementById('history-period').value;
      try {
        const response = await fetch('/api/motion?period=' + period, {signal: AbortSignal.timeout(5000)});
        if (!response.ok) throw new Error('Історія недоступна');
        const report = await response.json();
        if (request !== historyRequest) return;
        lastHistoryFetch = Date.now();
        document.getElementById('history-count').textContent = report.count + ' спрацювань';
        const chart = document.getElementById('motion-chart');
        chart.replaceChildren();
        chart.setAttribute('aria-label', report.count + ' спрацювань за вибраний період');
        const max = Math.max(1, ...report.buckets);
        report.buckets.forEach((count, index) => {
          const bar = document.createElement('div');
          bar.style.height = (3 + count / max * 57) + 'px';
          bar.className = count ? 'chart-bar active' : 'chart-bar';
          const date = new Date((report.start + index * report.bucket_seconds) * 1000);
          bar.title = date.toLocaleTimeString('uk-UA', {hour:'2-digit',minute:'2-digit'}) + ': ' + count;
          chart.append(bar);
        });
        const history = document.getElementById('pir-history');
        history.replaceChildren();
        for (const timestamp of report.events) {
          const row = document.createElement('li');
          const label = document.createElement('span'); label.textContent = 'Виявлено рух';
          const date = document.createElement('time');
          date.dateTime = new Date(timestamp * 1000).toISOString();
          date.textContent = new Date(timestamp * 1000).toLocaleString('uk-UA');
          row.append(label, date); history.append(row);
        }
        if (!report.events.length) {
          const row = document.createElement('li'); row.textContent = 'За цей період спрацювань немає'; history.append(row);
        }
      } catch (error) {
        if (request === historyRequest) document.getElementById('history-count').textContent = 'Не вдалося оновити історію';
      }
    }
    async function updateStatus() {
      if (statusBusy) return;
      statusBusy = true;
      const badge = document.getElementById('connection-status');
      try {
        const res = await fetch('/api/status', {signal: AbortSignal.timeout(5000)});
        if (!res.ok) throw new Error('Status unavailable');
        const data = await res.json();
        document.body.classList.remove('data-stale');
        badge.textContent = 'Підключено';
        badge.title = 'Оновлено: ' + new Date().toLocaleTimeString('uk-UA');

        // System stats

        const hardware = data.hardware || {};
        // PIR banner
        const pirBanner = document.getElementById('pir-banner');
        const pirText   = document.getElementById('pir-text');
        const pirCard   = document.getElementById('pir-module-card');
        const pirStatus = document.getElementById('pir-module-status');
        if (hardware.pir) {
          pirBanner.classList.remove('active');
          pirCard.classList.remove('pir-highlight');
          pirText.textContent = hardware.pir;
          pirStatus.textContent = hardware.pir;
        } else if (data.pir_state) {
          pirBanner.classList.add('active');
          pirText.textContent   = '🟢 РУХ ВИЯВЛЕНО!';
          pirCard.classList.add('pir-highlight');
          pirStatus.textContent = '🟡 РУХ ВИЯВЛЕНО!';
          pirStatus.style.color = '#ffd600';
        } else {
          pirBanner.classList.remove('active');
          pirText.textContent   = '🔵 Спокій (руху немає)';
          pirCard.classList.remove('pir-highlight');
          pirStatus.textContent = '⬤ Очікування...';
          pirStatus.style.color = '';
        }
        document.getElementById('stat-pir-count').textContent = data.pir_count;
        document.getElementById('stat-pir-last').textContent  = data.pir_last;
        document.getElementById('chk-auto-alarm').checked     = data.auto_alarm;

        const historyKey = JSON.stringify(data.pir_history || []);
        if (historyKey !== lastHistoryKey || Date.now() - lastHistoryFetch > 30000) {
          lastHistoryKey = historyKey;
          loadMotionHistory();
        }
        document.getElementById('fan-power-state').textContent =
          hardware.tachometer ? hardware.tachometer : data.fan_rpm > 0 ? 'Працює' :
          data.fan_rpm === 0 && data.fan_pwm > 0 ? 'Немає обертів' :
          data.fan_rpm === 0 && data.fan_pwm === 0 ? 'Зупинений' : 'Невідомо';

        // Fan stats
        document.getElementById('fan-temp').textContent  = data.cpu_temp ? data.cpu_temp + '°C' : '-';
        document.getElementById('fan-state').textContent = data.fan_state === null ? '-' : 'Рівень ' + data.fan_state + '/4';
        document.getElementById('fan-auto').disabled = data.fan_state === null;
        document.getElementById('fan-on').disabled = data.fan_state === null;
        document.getElementById('fan-off').disabled = data.fan_state === null;
        document.getElementById('fan-pwm').textContent = data.fan_pwm ?? '-';
        document.getElementById('fan-rpm').textContent = data.fan_rpm ?? '-';
        updatePinoutState(data.gpio || {});

        // Virtual LEDs
        const mode = hardware.spi ? 'unknown' : data.led_mode;
        const slider = document.getElementById('led-brightness');
        if (document.activeElement !== slider) {
          slider.value = data.led_brightness;
          document.getElementById('brightness-value').textContent = data.led_brightness + '%';
        }
        slider.disabled = Boolean(hardware.spi && hardware.spi === 'SPI недоступний');
        document.getElementById('led-hardware-status').textContent = hardware.spi || 'SPI доступний · підтверджується передача команди';
        document.getElementById('buzzer-hardware-status').textContent = hardware.buzzer || 'GPIO доступний';
        document.querySelectorAll('[onclick^="triggerBuzzer"]').forEach(button => button.disabled = Boolean(hardware.buzzer));
        document.getElementById('chk-auto-alarm').disabled = Boolean(hardware.pir);
        const color = (data.led_color || []).join(',');
        let ledLabel = mode === 'unknown' ? 'Стан не підтверджено' : mode === 'rainbow' ? 'Веселка' : mode === 'off' ? 'Вимкнено' : 'Власний колір';
        document.querySelectorAll('.led-presets button').forEach(button => {
          const active = button.dataset.ledMode === mode || (mode === 'color' && button.dataset.ledColor === color);
          button.setAttribute('aria-pressed', String(active));
          if (active && mode === 'color') ledLabel = button.textContent;
        });
        document.getElementById('led-mode-label').textContent = ledLabel;
        const preview = document.getElementById('led-preview');
        preview.classList.toggle('rainbow', mode === 'rainbow');
        preview.style.backgroundColor = mode === 'color' ? `rgb(${color})` : '';

        if (mode === 'rainbow') {
          startVledRainbow();
        } else {
          stopVledRainbow();
          if (mode === 'color' && data.led_color) {
            const [r,g,b] = data.led_color;
            setVled(0, r, g, b);
            setVled(1, r, g, b);
          } else {
            setVled(0, 0, 0, 0);
            setVled(1, 0, 0, 0);
          }
        }

      } catch (e) {
        badge.textContent = 'Немає зв’язку';
        document.body.classList.add('data-stale');
        console.error('Update error:', e);
      } finally { statusBusy = false; }
    }

    async function sendControl(path, payload, statusId = 'control-status') {
      const status = document.getElementById(statusId);
      try {
        const response = await fetch(path, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error);
        status.textContent = '';
        return true;
      } catch (error) {
        status.textContent = error.message;
        return false;
      }
    }

    async function setFan(enabled) {
      await sendControl('/api/fan', enabled === 'auto' ? {mode:'auto'} : { enabled }, 'fan-control-status');
      await updateStatus();
    }

    function triggerBuzzer(mode) {
      return sendControl('/api/buzzer/' + mode);
    }

    function setLedMode(mode) {
      return sendControl('/api/leds/' + mode, undefined, 'led-control-status');
    }

    function setLedColor(r, g, b) {
      return sendControl('/api/leds/color', { r, g, b }, 'led-control-status');
    }

    async function setBrightness(value) {
      await sendControl('/api/leds/brightness', {value}, 'led-control-status');
      await updateStatus();
    }
    async function toggleAutoAlarm(enabled) {
      if (!await sendControl('/api/alarm_mode', { enabled })) {
        updateStatus();
      }
    }

    // Boot
    setInterval(updateStatus, 800);
    updateStatus();
  </script>
</body>
</html>
"""

class DashboardHandler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/" or parsed.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_TEMPLATE.encode("utf-8"))
        elif parsed.path == "/api/motion":
            period = parse_qs(parsed.query).get("period", ["hour"])[0]
            try:
                self._json(motion_report(period))
            except ValueError:
                self._json({"error": "Некоректний період"}, status=400)
        elif parsed.path == "/api/status":
            status = get_system_status()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(json.dumps(status).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        if (parsed.path.startswith("/api/buzzer/") or parsed.path == "/api/alarm_mode") and gpio_req is None:
            self._json({"ok": False, "error": "GPIO недоступний"}, status=503)
            return
        if parsed.path.startswith("/api/leds/") and spi is None:
            self._json({"ok": False, "error": "SPI недоступний"}, status=503)
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
            if not 0 <= length <= 1024:
                raise ValueError("Invalid body length")
            payload = json.loads(self.rfile.read(length)) if length else {}
            if not isinstance(payload, dict):
                raise ValueError("Expected a JSON object")
        except (ValueError, UnicodeError):
            self._json({"ok": False, "error": "Некоректний запит"}, status=400)
            return

        if parsed.path == "/api/fan":
            enabled = payload.get("enabled")
            automatic = payload == {"mode": "auto"}
            if self.headers.get_content_type() != "application/json" or not (automatic or isinstance(enabled, bool)):
                self._json({"ok": False, "error": "Некоректний стан кулера"}, status=400)
                return
            mode = "auto" if automatic else "on" if enabled else "off"
            try:
                host_request("POST", "/fan", {"mode": mode})
            except (OSError, ValueError, http.client.HTTPException):
                self._json({"ok": False, "error": "Керування кулером недоступне"}, status=503)
            else:
                self._json({"ok": True})
        elif parsed.path == "/api/buzzer/beep":
            play_buzzer_beep(times=1, duration=0.1)
            self._json({"ok": True})
        elif parsed.path == "/api/buzzer/alarm":
            play_buzzer_alarm()
            self._json({"ok": True})
        elif parsed.path.startswith("/api/leds/"):
            try:
                self._handle_led(parsed.path, payload)
            except OSError as error:
                self._json({"ok": False, "error": str(error)}, status=503)
        elif parsed.path == "/api/alarm_mode":
            global auto_alarm_enabled
            enabled = payload.get("enabled")
            if not isinstance(enabled, bool):
                self._json({"ok": False, "error": "Некоректний стан сигналізації"}, status=400)
                return
            auto_alarm_enabled = enabled
            self._json({"ok": True, "auto_alarm": auto_alarm_enabled})
        else:
            self.send_response(404)
            self.end_headers()

    def _handle_led(self, path, payload):
        if path == "/api/leds/brightness":
            value = payload.get("value")
            if type(value) is not int or not 0 <= value <= 100:
                self._json({"ok": False, "error": "Яскравість має бути від 0 до 100"}, status=400)
                return
            set_led_brightness(value)
            self._json({"ok": True})
        elif path == "/api/leds/rainbow":
            start_rainbow()
            self._json({"ok": True})
        elif path == "/api/leds/off":
            turn_leds_off()
            self._json({"ok": True})
        elif path == "/api/leds/color":
            color = [payload.get(channel) for channel in ("r", "g", "b")]
            if any(type(value) is not int or not 0 <= value <= 255 for value in color):
                self._json({"ok": False, "error": "Компоненти RGB мають бути цілими числами від 0 до 255"}, status=400)
                return
            set_led_solid_color(*color)
            self._json({"ok": True})
        else:
            self._json({"ok": False, "error": "Невідома команда LED"}, status=404)

    def _json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

    def log_message(self, format, *args):
        pass

def main():
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    server = HTTPServer(("0.0.0.0", PORT), DashboardHandler)
    print(f"[Dashboard] Starting Pi 5 Sensor & Pinout Dashboard on http://0.0.0.0:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        try:
            turn_leds_off()
        except OSError:
            pass

if __name__ == "__main__":
    main()
