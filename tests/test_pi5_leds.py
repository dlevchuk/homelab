"""LED commands commit only after SPI succeeds; brightness preserves source RGB."""
import ast
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import Mock

SCRIPT = Path(__file__).resolve().parents[1] / 'ansible/raspberry/roles/pi5_dashboard/files/pi5_dashboard.py'


class LedCommands(unittest.TestCase):
    def setUp(self):
        names = {'send_ws2813_pixels', 'rainbow_pixels', 'rainbow_loop', 'start_rainbow',
                 'set_led_solid_color', 'turn_leds_off', 'set_led_brightness'}
        tree = ast.parse(SCRIPT.read_text())
        functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
        self.ns = dict(spi=Mock(), hw_lock=threading.Lock(), led_lock=threading.RLock(),
                       led_error=None, led_brightness=100, led_generation=0, led_current_mode='off',
                       led_color=[0, 0, 0], BIT_0=0xC0, BIT_1=0xFC, threading=Mock(), time=time)
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(SCRIPT), 'exec'), self.ns)

    def test_failed_transfer_keeps_previous_mode_and_color(self):
        self.ns['set_led_solid_color'](10, 20, 30)
        self.ns['spi'].xfer2.side_effect = OSError('transfer failed')
        with self.assertRaises(OSError):
            self.ns['turn_leds_off']()
        self.assertEqual(self.ns['led_current_mode'], 'color')
        self.assertEqual(self.ns['led_color'], [10, 20, 30])
        self.assertIsNotNone(self.ns['led_error'])
        self.ns['spi'].xfer2.side_effect = None
        self.ns['turn_leds_off']()
        self.assertIsNone(self.ns['led_error'])

    def test_brightness_scales_wire_bytes_and_keeps_rgb(self):
        self.ns['set_led_solid_color'](255, 0, 0)
        self.ns['set_led_brightness'](50)
        frame = self.ns['spi'].xfer2.call_args.args[0]
        self.assertEqual(frame[8:16], [0xC0] + [0xFC] * 7)  # R=127 in GRB order
        self.assertEqual(self.ns['led_color'], [255, 0, 0])
        self.ns['spi'].xfer2.side_effect = OSError('failed')
        with self.assertRaises(OSError):
            self.ns['set_led_brightness'](10)
        self.assertEqual(self.ns['led_brightness'], 50)

    def test_old_rainbow_worker_cannot_overwrite_new_command(self):
        self.ns['led_generation'] = 2
        self.ns['led_current_mode'] = 'rainbow'
        self.ns['rainbow_loop'](1)
        self.ns['spi'].xfer2.assert_not_called()

    def test_no_spi_is_an_error(self):
        self.ns['spi'] = None
        with self.assertRaises(OSError):
            self.ns['set_led_solid_color'](255, 0, 0)
        self.assertEqual(self.ns['led_current_mode'], 'off')
