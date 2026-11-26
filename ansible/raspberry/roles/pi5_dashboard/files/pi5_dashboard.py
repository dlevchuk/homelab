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
import threading
import subprocess
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

PORT = int(os.environ.get("DASHBOARD_PORT", "8080"))

# Hardware locks and state
hw_lock = threading.Lock()
pir_count = 0
pir_last_time = "Ніколи"
pir_current_state = False
auto_alarm_enabled = False
led_current_mode = "off"  # "off", "color", "rainbow"
led_color = [0, 0, 0]
rainbow_running = False

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

def send_ws2813_pixels(pixels):
    """pixels: list of (r, g, b) tuples"""
    if not spi:
        return
    buf = bytearray()
    for (r, g, b) in pixels:
        for byte_val in (g, r, b):
            for bit_pos in range(7, -1, -1):
                buf.append(BIT_1 if (byte_val >> bit_pos) & 1 else BIT_0)
    buf.extend(bytearray([0x00] * 300))
    with hw_lock:
        try:
            spi.xfer2(list(buf))
        except Exception as e:
            print(f"[SPI] xfer error: {e}")

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

def rainbow_loop():
    global rainbow_running
    rainbow_running = True
    def _wheel(pos):
        pos = 255 - pos
        if pos < 85:
            return (255 - pos * 3, 0, pos * 3)
        if pos < 170:
            pos -= 85
            return (0, pos * 3, 255 - pos * 3)
        pos -= 170
        return (pos * 3, 255 - pos * 3, 0)

    step = 0
    while rainbow_running and led_current_mode == "rainbow":
        c1 = _wheel(step & 255)
        c2 = _wheel((step + 128) & 255)
        send_ws2813_pixels([c1, c2])
        step = (step + 4) % 256
        time.sleep(0.04)
    rainbow_running = False

def start_rainbow():
    global led_current_mode
    led_current_mode = "rainbow"
    if not rainbow_running:
        threading.Thread(target=rainbow_loop, daemon=True).start()

def set_led_solid_color(r, g, b):
    global led_current_mode, led_color, rainbow_running
    rainbow_running = False
    led_current_mode = "color"
    led_color = [r, g, b]
    send_ws2813_pixels([(r, g, b), (r, g, b)])

def turn_leds_off():
    global led_current_mode, led_color, rainbow_running
    rainbow_running = False
    led_current_mode = "off"
    led_color = [0, 0, 0]
    send_ws2813_pixels([(0, 0, 0), (0, 0, 0)])

# Background worker for HC-SR505 motion tracking
def pir_monitor_thread():
    global pir_count, pir_last_time, pir_current_state
    last_val = False
    while True:
        try:
            if gpio_req:
                val = (gpio_req.get_value(25) == gpiod.line.Value.ACTIVE)
                pir_current_state = val
                if val and not last_val:
                    pir_count += 1
                    pir_last_time = time.strftime('%H:%M:%S')
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
            pass
        time.sleep(0.1)

threading.Thread(target=pir_monitor_thread, daemon=True).start()

def get_system_status():
    # 1. CPU temp
    cpu_temp = None
    try:
        out = subprocess.check_output(['vcgencmd', 'measure_temp'], text=True)
        cpu_temp = out.replace("temp=", "").replace("'C", "").strip()
    except:
        try:
            with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                cpu_temp = f"{float(f.read().strip()) / 1000.0:.1f}"
        except: pass

    # 2. Fan status
    fan_state = 0
    fan_pwm = 0
    try:
        with open('/sys/class/thermal/cooling_device0/cur_state', 'r') as f:
            fan_state = int(f.read().strip())
    except: pass
    try:
        with open('/sys/class/hwmon/hwmon2/pwm1', 'r') as f:
            fan_pwm = int(f.read().strip())
    except:
        for p in ('/sys/class/hwmon/hwmon1/pwm1', '/sys/class/hwmon/hwmon3/pwm1'):
            if os.path.exists(p):
                try:
                    with open(p, 'r') as f: fan_pwm = int(f.read().strip())
                    break
                except: pass

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

    return {
        "pir_state": pir_current_state,
        "pir_count": pir_count,
        "pir_last": pir_last_time,
        "auto_alarm": auto_alarm_enabled,
        "led_mode": led_current_mode,
        "led_color": led_color,
        "cpu_temp": cpu_temp,
        "fan_state": fan_state,
        "fan_pwm": fan_pwm,
        "uptime": uptime_str,
        "throttled": throttled
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
    button:hover { background: #323a45; border-color: #536074; transform: translateY(-1px); }
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
    .pinout-container {
      background: #2b3d26;
      border-radius: 8px;
      padding: 12px;
      color: #fff;
      box-shadow: inset 0 0 12px rgba(0,0,0,0.6);
      border: 1px solid #3c5435;
    }
    .pinout-header {
      display: flex; align-items: center; gap: 10px; margin-bottom: 10px;
      padding-bottom: 6px; border-bottom: 1px solid rgba(255,255,255,0.2);
    }
    .pinout-title { font-size: 14px; font-weight: 700; line-height: 1.2; }
    .pinout-subtitle { font-size: 10px; color: rgba(255,255,255,0.75); }
    .pinout-board { display: flex; flex-direction: column; gap: 2.5px; }
    .pin-row { display: grid; grid-template-columns: 1fr 22px 22px 1fr; gap: 4px; align-items: center; }
    .pin-label {
      font-size: 10.5px; font-weight: 500; padding: 2px 6px; border-radius: 4px;
      display: flex; align-items: center; gap: 4px;
      white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    }
    .pin-label.left { justify-content: flex-end; text-align: right; }
    .pin-label.right { justify-content: flex-start; text-align: left; }
    .pin-sub { font-size: 9px; opacity: 0.8; font-weight: normal; }
    .pin-circle {
      width: 18px; height: 18px; border-radius: 50%;
      display: flex; align-items: center; justify-content: center;
      font-size: 8px; font-weight: bold; cursor: pointer; position: relative;
      border: 1.5px solid rgba(0,0,0,0.5);
      transition: transform 0.15s, box-shadow 0.15s;
    }
    .pin-circle:hover { transform: scale(1.3); z-index: 10; box-shadow: 0 0 8px #fff; }
    .pin-circle .pin-dot { width: 5px; height: 5px; border-radius: 50%; background: #fff; }
    .pin-circle.rect { border-radius: 3px; }
    .p-pwr5 { background: #e53935; }
    .p-pwr3 { background: #fb8c00; }
    .p-gnd { background: #212121; border-color: #111; }
    .p-gpio { background: #7cb342; }
    .p-spi { background: #d81b60; }
    .p-pcm { background: #00897b; }
    .p-i2c { background: #5c6bc0; }
    .p-uart { background: #ef6c00; }
    .pin-tag {
      font-size: 8px; padding: 1px 4px; border-radius: 3px; font-weight: 700; letter-spacing: 0.02em;
    }
    .pin-tag.active-dev {
      background: #ffd600; color: #000; box-shadow: 0 0 6px #ffd600;
    }
    #pin-info-bar {
      margin-top: 10px; background: rgba(0,0,0,0.5); border-radius: 6px;
      padding: 6px 10px; font-size: 11px; color: #e0e0e0;
    }
  </style>
</head>
<body>

  <header>
    <div class="header-title">
      <h1>🍓 Raspberry Pi 5 • Лабораторія</h1>
      <span class="badge">Online</span>
    </div>
    <div class="header-stats">
      <span>CPU: <b id="stat-temp">-</b></span>
      <span>Uptime: <b id="stat-uptime">-</b></span>
      <span>Throttled: <b id="stat-throttled">-</b></span>
    </div>
  </header>

  <div class="main-grid">
    <!-- Ліва колонка: Керування датчиками та пристроями -->
    <div class="col">

      <div id="control-status" role="status"></div>

      <!-- Датчик руху HC-SR505 -->
      <div class="card">
        <div class="card-head">
          <h2>👁️ Датчик руху HC-SR505</h2>
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
            <span class="stat-label">Спрацьовувань</span>
            <span class="stat-val" id="stat-pir-count">0</span>
          </div>
          <div class="stat-box">
            <span class="stat-label">Останній рух</span>
            <span class="stat-val" id="stat-pir-last" style="font-size:14px">-</span>
          </div>
        </div>
      </div>

      <!-- Адресні діоди WS2813 -->
      <div class="card">
        <div class="card-head">
          <h2>💡 Адресні світлодіоди WS2813-2</h2>
          <span style="font-size:12px; color:var(--text-dim)">SPI MOSI (Pin 19)</span>
        </div>
        <p style="font-size:12.5px; color:var(--text-dim); margin-bottom:10px;">Керування 2x RGB NeoPixel діодами через апаратний SPI на 6.4 МГц:</p>
        <div class="btn-row">
          <button class="btn-rainbow" onclick="setLedMode('rainbow')">🌈 Веселка (Rainbow)</button>
          <button class="btn-green" onclick="setLedColor(0, 255, 0)">🟢 Зелений</button>
          <button class="btn-blue" onclick="setLedColor(0, 120, 255)">🔵 Синій</button>
          <button class="btn-red" onclick="setLedColor(255, 0, 0)">🔴 Червоний</button>
          <button style="background:#fbc02d; color:#000;" onclick="setLedColor(255, 180, 0)">🟡 Жовтий</button>
          <button style="background:#ab47bc;" onclick="setLedColor(200, 0, 255)">🟣 Фіолетовий</button>
          <button style="background:#ffffff; color:#000;" onclick="setLedColor(200, 200, 200)">⚪ Білий</button>
          <button class="btn-red" onclick="setLedMode('off')">⏹ Вимкнути</button>
        </div>
      </div>

      <!-- Зумер MH-FMD -->
      <div class="card">
        <div class="card-head">
          <h2>🔔 Зумер (MH-FMD Buzzer)</h2>
          <span style="font-size:12px; color:var(--text-dim)">Pin 18 (GPIO 24)</span>
        </div>
        <p style="font-size:12.5px; color:var(--text-dim); margin-bottom:10px;">Генерація звукових попереджень:</p>
        <div class="btn-row">
          <button class="btn-green" onclick="triggerBuzzer('beep')">🔔 Короткий сигнал</button>
          <button class="btn-red" onclick="triggerBuzzer('alarm')">🚨 Сирена тривоги</button>
        </div>
      </div>

      <!-- Вентилятор SC1148 Active Cooler -->
      <div class="card">
        <div class="card-head">
          <h2>🌀 Active Cooler SC1148</h2>
          <span style="font-size:12px; color:var(--text-dim)">ШІМ Pin 12 (GPIO 18)</span>
        </div>
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
        </div>
      </div>

    </div>

    <!-- Права колонка: Схема підключення + 40-Pin Pinout -->
    <div class="col">
      <div class="card" style="padding: 14px;">

        <!-- Tab bar -->
        <div class="tab-bar">
          <button class="tab-btn active" onclick="switchTab('circuit', this)">🔌 Схема: Pi 5 + Макетка</button>
          <button class="tab-btn" onclick="switchTab('pinout', this)">📌 40-Pin GPIO Pinout</button>
        </div>

        <!-- Tab: Circuit Diagram -->
        <div class="tab-panel active" id="tab-circuit">
          <div class="circuit-wrap">

            <!-- Pi 5 Pins -->
            <div class="pi5-box">
              <div class="pi5-box-title">🍓 Raspberry Pi 5</div>

              <div class="pi5-pin-row">
                <div class="pi5-pin-num">6</div>
                <div class="pi5-wire wire-black"></div>
                <div class="pi5-pin-label">GND (Земля)</div>
              </div>
              <div class="pi5-pin-row">
                <div class="pi5-pin-num">12</div>
                <div class="pi5-wire wire-orange"></div>
                <div class="pi5-pin-label">GPIO 18 • PWM Кулер</div>
              </div>
              <div class="pi5-pin-row">
                <div class="pi5-pin-num">16</div>
                <div class="pi5-wire wire-yellow"></div>
                <div class="pi5-pin-label">GPIO 23 • Tach кулер</div>
              </div>
              <div class="pi5-pin-row">
                <div class="pi5-pin-num">18</div>
                <div class="pi5-wire wire-blue"></div>
                <div class="pi5-pin-label">GPIO 24 • Зумер</div>
              </div>
              <div class="pi5-pin-row">
                <div class="pi5-pin-num">19</div>
                <div class="pi5-wire wire-purple"></div>
                <div class="pi5-pin-label">GPIO 10 • SPI MOSI LED</div>
              </div>
              <div class="pi5-pin-row">
                <div class="pi5-pin-num">22</div>
                <div class="pi5-wire wire-green"></div>
                <div class="pi5-pin-label">GPIO 25 • PIR OUT</div>
              </div>
            </div>

            <div class="circuit-arrow">➜</div>

            <!-- Breadboard + Modules -->
            <div class="breadboard-wrap">
              <div class="breadboard">
                <div class="bb-title">📋 MB-102 Breadboard</div>

                <!-- YwRobot Power Module (spans full width) -->
                <div class="ywrobot-card">
                  <div class="ywrobot-title">
                    ⚡ YwRobot 545043 Power Module
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
                    <span class="bb-rail-desc">Спільна земля з Pi 5 Pin 6</span>
                  </div>
                </div>

                <!-- Module Cards -->
                <div class="bb-modules">

                  <!-- SC1148 Active Cooler -->
                  <div class="bb-module">
                    <div class="bb-mod-title">🌀 SC1148 Active Cooler</div>
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
                      <span>Tach → Pin 16 (GPIO 23)</span>
                    </div>
                  </div>

                  <!-- MH-FMD Buzzer -->
                  <div class="bb-module">
                    <div class="bb-mod-title">🔔 MH-FMD Зумер</div>
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
                  </div>

                  <!-- WS2813-2 LEDs -->
                  <div class="bb-module">
                    <div class="bb-mod-title">💡 WS2813-2 LED × 2</div>
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
                  </div>

                  <!-- HC-SR505 PIR -->
                  <div class="bb-module" id="pir-module-card">
                    <div class="bb-mod-title">👁️ HC-SR505 PIR</div>
                    <div class="bb-mod-row">
                      <div class="bb-mod-dot dot-red"></div>
                      <span>+ ← рейка +5V</span>
                    </div>
                    <div class="bb-mod-row">
                      <div class="bb-mod-dot dot-black"></div>
                      <span>GND (середній)</span>
                    </div>
                    <div class="bb-mod-row">
                      <div class="bb-mod-dot dot-green"></div>
                      <span>OUT → Pin 22 (GPIO 25)</span>
                    </div>
                    <div style="margin-top:5px; font-size:9px; color:var(--text-dim);" id="pir-module-status">
                      ⬤ Очікування...
                    </div>
                  </div>

                </div><!-- /bb-modules -->
              </div><!-- /breadboard -->
            </div><!-- /breadboard-wrap -->
          </div><!-- /circuit-wrap -->
        </div><!-- /tab-circuit -->

        <!-- Tab: 40-pin Pinout -->
        <div class="tab-panel" id="tab-pinout">
          <div class="pinout-container">
            <div class="pinout-header">
              <div>
                <div class="pinout-title">Raspberry Pi 5 Pinout</div>
                <div class="pinout-subtitle">Поточний стан 40-пінової гребінки та підключення</div>
              </div>
            </div>
            <div class="pinout-board" id="pinout-board">
              <!-- Генерується JS -->
            </div>
            <div id="pin-info-bar">
              💡 Наведіть курсор або натисніть на пін для інформації
            </div>
          </div>
        </div><!-- /tab-pinout -->

      </div><!-- /card -->
    </div><!-- /right col -->
  </div><!-- /main-grid -->

  <script>
    // ===== TABS =====
    function switchTab(id, btn) {
      document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      document.getElementById('tab-' + id).classList.add('active');
      btn.classList.add('active');
      if (id === 'pinout' && !pinoutRendered) renderPinout();
    }
    let pinoutRendered = false;

    // ===== PINOUT =====
    const PINOUT_DEFS = [
      { pin: 1,  name: "3v3 Power",  sub: "3.3V DC", type: "pwr3" },
      { pin: 2,  name: "",          sub: "", type: "pwr5" },
      { pin: 3,  name: "GPIO 2",     sub: "SDA / I2C1", type: "i2c" },
      { pin: 4,  name: "",          sub: "", type: "pwr5" },
      { pin: 5,  name: "GPIO 3",     sub: "SCL / I2C1", type: "i2c" },
      { pin: 6,  name: "Ground",     sub: "0V GND • Спільна земля з макеткою", type: "gnd", device: "⏚ GND макетки" },
      { pin: 7,  name: "GPIO 4",     sub: "GPCLK0", type: "gpio" },
      { pin: 8,  name: "GPIO 14",    sub: "TXD / UART", type: "uart" },
      { pin: 9,  name: "Ground",     sub: "0V GND", type: "gnd" },
      { pin: 10, name: "GPIO 15",    sub: "RXD / UART", type: "uart" },
      { pin: 11, name: "GPIO 17",    sub: "General IO", type: "gpio" },
      { pin: 12, name: "GPIO 18",    sub: "PWM0 • ШІМ Active Cooler", type: "pcm", device: "🌀 Кулер (PWM0)" },
      { pin: 13, name: "GPIO 27",    sub: "General IO", type: "gpio" },
      { pin: 14, name: "Ground",     sub: "0V GND", type: "gnd" },
      { pin: 15, name: "GPIO 22",    sub: "General IO", type: "gpio" },
      { pin: 16, name: "GPIO 23",    sub: "Active Cooler Tach", type: "gpio", device: "🌀 Кулер (TACH)" },
      { pin: 17, name: "3v3 Power",  sub: "3.3V DC", type: "pwr3" },
      { pin: 18, name: "GPIO 24",    sub: "MH-FMD Buzzer", type: "gpio", device: "🔔 Зумер (Сигнал)" },
      { pin: 19, name: "GPIO 10",    sub: "SPI0 MOSI • WS2813 DIN", type: "spi", device: "💡 WS2813 (DIN)" },
      { pin: 20, name: "Ground",     sub: "0V GND", type: "gnd" },
      { pin: 21, name: "GPIO 9",     sub: "SPI0 MISO", type: "spi" },
      { pin: 22, name: "GPIO 25",    sub: "HC-SR505 PIR Motion", type: "gpio", device: "👁️ Датчик руху (OUT)" },
      { pin: 23, name: "GPIO 11",    sub: "SPI0 SCLK", type: "spi" },
      { pin: 24, name: "GPIO 8",     sub: "SPI0 CE0", type: "spi" },
      { pin: 25, name: "Ground",     sub: "0V GND", type: "gnd" },
      { pin: 26, name: "GPIO 7",     sub: "SPI0 CE1", type: "spi" },
      { pin: 27, name: "GPIO 0",     sub: "ID_SD", type: "i2c" },
      { pin: 28, name: "GPIO 1",     sub: "ID_SC", type: "i2c" },
      { pin: 29, name: "GPIO 5",     sub: "General IO", type: "gpio" },
      { pin: 30, name: "Ground",     sub: "0V GND", type: "gnd" },
      { pin: 31, name: "GPIO 6",     sub: "General IO", type: "gpio" },
      { pin: 32, name: "GPIO 12",    sub: "PWM0_CHAN0", type: "pcm" },
      { pin: 33, name: "GPIO 13",    sub: "PWM0_CHAN1", type: "pcm" },
      { pin: 34, name: "Ground",     sub: "0V GND", type: "gnd" },
      { pin: 35, name: "GPIO 19",    sub: "PCM FS", type: "pcm" },
      { pin: 36, name: "GPIO 16",    sub: "General IO", type: "gpio" },
      { pin: 37, name: "GPIO 26",    sub: "General IO", type: "gpio" },
      { pin: 38, name: "GPIO 20",    sub: "PCM DIN", type: "pcm" },
      { pin: 39, name: "Ground",     sub: "0V GND", type: "gnd" },
      { pin: 40, name: "GPIO 21",    sub: "PCM DOUT", type: "pcm" }
    ];

    function renderPinout() {
      const board = document.getElementById('pinout-board');
      let html = '';
      for (let i = 0; i < PINOUT_DEFS.length; i += 2) {
        const left  = PINOUT_DEFS[i];
        const right = PINOUT_DEFS[i+1];
        const leftShape = left.pin === 1 ? 'rect' : '';
        const leftTag  = left.device  ? `<span class="pin-tag active-dev">${left.device}</span>`  : '';
        const rightTag = right.device ? `<span class="pin-tag active-dev">${right.device}</span>` : '';
        html += `
          <div class="pin-row">
            <div class="pin-label left" id="pin-lbl-${left.pin}">
              ${leftTag}
              <span class="pin-name">${left.name}</span>
            </div>
            <div class="pin-circle p-${left.type} ${leftShape}" onmouseover="showPin(${left.pin})">
              <div class="pin-dot"></div>
            </div>
            <div class="pin-circle p-${right.type}" onmouseover="showPin(${right.pin})">
              <div class="pin-dot"></div>
            </div>
            <div class="pin-label right" id="pin-lbl-${right.pin}">
              <span class="pin-name">${right.name}</span>
              ${rightTag}
            </div>
          </div>
        `;
      }
      board.innerHTML = html;
      pinoutRendered = true;
    }

    function showPin(pinNum) {
      const p = PINOUT_DEFS.find(item => item.pin === pinNum);
      if (!p) return;
      const detail = p.name ? `: ${p.name} (${p.sub})` : '';
      const dev = p.device ? ` ➜ <b>${p.device}</b>` : '';
      document.getElementById('pin-info-bar').innerHTML =
        `<b>Pin ${p.pin}</b>${detail}${dev}`;
    }

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
    async function updateStatus() {
      try {
        const res  = await fetch('/api/status');
        const data = await res.json();

        // System stats
        document.getElementById('stat-temp').textContent      = data.cpu_temp ? data.cpu_temp + '°C' : '-';
        document.getElementById('stat-uptime').textContent    = data.uptime   || '-';
        document.getElementById('stat-throttled').textContent = data.throttled || '-';

        // PIR banner
        const pirBanner = document.getElementById('pir-banner');
        const pirText   = document.getElementById('pir-text');
        const pirCard   = document.getElementById('pir-module-card');
        const pirStatus = document.getElementById('pir-module-status');
        if (data.pir_state) {
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

        // Fan stats
        document.getElementById('fan-temp').textContent  = data.cpu_temp ? data.cpu_temp + '°C' : '-';
        document.getElementById('fan-state').textContent = 'Рівень ' + data.fan_state + '/4';
        document.getElementById('fan-pwm').textContent   = data.fan_pwm;

        // Virtual LEDs
        const mode = data.led_mode;
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
        console.error('Update error:', e);
      }
    }

    async function sendControl(path, payload) {
      const status = document.getElementById('control-status');
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

    function triggerBuzzer(mode) {
      return sendControl('/api/buzzer/' + mode);
    }

    function setLedMode(mode) {
      return sendControl('/api/leds/' + mode);
    }

    function setLedColor(r, g, b) {
      return sendControl('/api/leds/color', { r, g, b });
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

        if parsed.path == "/api/buzzer/beep":
            play_buzzer_beep(times=1, duration=0.1)
            self._json({"ok": True})
        elif parsed.path == "/api/buzzer/alarm":
            play_buzzer_alarm()
            self._json({"ok": True})
        elif parsed.path == "/api/leds/rainbow":
            start_rainbow()
            self._json({"ok": True})
        elif parsed.path == "/api/leds/off":
            turn_leds_off()
            self._json({"ok": True})
        elif parsed.path == "/api/leds/color":
            color = [payload.get(channel) for channel in ("r", "g", "b")]
            if any(type(value) is not int or not 0 <= value <= 255 for value in color):
                self._json({"ok": False, "error": "Компоненти RGB мають бути цілими числами від 0 до 255"}, status=400)
                return
            set_led_solid_color(*color)
            self._json({"ok": True})
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

    def _json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

    def log_message(self, format, *args):
        pass

def main():
    server = HTTPServer(("0.0.0.0", PORT), DashboardHandler)
    print(f"[Dashboard] Starting Pi 5 Sensor & Pinout Dashboard on http://0.0.0.0:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        turn_leds_off()

if __name__ == "__main__":
    main()
