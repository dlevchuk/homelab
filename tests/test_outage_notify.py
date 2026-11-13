"""Offline scenarios for the durable outage monitor."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

SCRIPT = Path(__file__).resolve().parents[1] / 'ansible/raspberry/roles/outage_notify/files/raspberry_outage.py'
spec = importlib.util.spec_from_file_location('outage_notify', SCRIPT)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def previous(**changes):
    result = dict(clean=True, panic=False, ping_reboot=False, last_ts=1000,
                  src='журнал', undervolt=False, flaps=0)
    result.update(changes)
    return result


def state(boot_event=True):
    return dict(boot='a', online=None, outage=None,
                queue=[dict(boot='a', prev=previous())] if boot_event else [])


class Messages(unittest.TestCase):
    def test_causes_and_priority(self):
        for flags, text in [({}, 'штатного завершення'),
                            ({'clean': False}, 'нештатного завершення'),
                            ({'clean': False, 'ping_reboot': True}, 'ping-reboot'),
                            ({'panic': True, 'ping_reboot': True}, 'kernel panic')]:
            with self.subTest(flags=flags):
                message = m.build_message(previous(**flags), True, 40,
                                          boot_ts=1020, recovery_ts=1060)
                self.assertIn(text, message)
                self.assertIn('20 с', message)
                self.assertIn('40 с', message)
                self.assertNotIn('Pi був вимкнений', message)

    def test_first_boot_keeps_network_duration(self):
        self.assertIn('45 с', m.build_message(None, False, 45))

    def test_bad_previous_clock_is_not_zero_duration(self):
        message = m.build_message(previous(last_ts=3000), True, 40,
                                  boot_ts=1020, recovery_ts=1060)
        self.assertIn('час попереднього журналу некоректний', message)
        self.assertNotIn('до поточного запуску: 0', message)

    def test_seconds_minutes_days(self):
        for seconds, text in [(0, '0 с'), (59, '59 с'), (60, '1 хв'),
                              (3660, '1 год 1 хв'), (86400, '1 д 0 год 0 хв')]:
            self.assertEqual(m.fmt_duration(seconds), text)

    def test_ntp_failure_is_unknown(self):
        with patch.object(m.subprocess, 'run', return_value=Mock(returncode=1, stdout='')):
            self.assertFalse(m.time_synced())
        with patch.object(m.subprocess, 'run', side_effect=OSError):
            self.assertFalse(m.time_synced())

    def test_ping_marker_requires_ping_logger(self):
        with patch.object(m, 'read_heartbeat', return_value=None), patch.object(
                m, 'journal', side_effect=['Reached target Reboot. Rebooting system', '', 'Oops:']):
            result = m.analyze_boot(-1, dict(bid='old', journal_ts=1000, uptime=700))
        self.assertFalse(result['ping_reboot'])
        self.assertFalse(result['panic'])


class NetworkTransitions(unittest.TestCase):
    def sample(self, s, online, mono, synced=True, bid='a', epoch=None):
        m.record_sample(s, bid, online, synced,
                        1000 + mono if epoch is None else epoch, mono, mono)

    def test_boot_online_and_no_duplicate_on_next_sample(self):
        s = state()
        self.sample(s, True, 40)
        self.assertIn('40 с', s['queue'][0]['text'])
        self.sample(s, True, 50)
        self.assertEqual(len(s['queue']), 1)

    def test_outage_and_recovery_without_reboot(self):
        s = state(False)
        self.sample(s, True, 10)
        self.sample(s, False, 20)
        self.sample(s, False, 50)
        self.sample(s, True, 95)
        self.assertEqual(len(s['queue']), 1)
        self.assertIn('1 хв', s['queue'][0]['text'])
        self.assertIsNone(s['outage'])

    def test_ntp_wait_does_not_inflate_outage(self):
        s = state()
        self.sample(s, False, 20, False)
        self.sample(s, True, 50, False)
        self.assertNotIn('text', s['queue'][0])
        self.sample(s, True, 150, True)
        self.assertEqual(s['queue'][0]['ready_ts'], 1150)
        self.assertIn('за перевірками): 30 с', s['queue'][0]['text'])
        self.assertIn('через 50 с', s['queue'][0]['text'])
        self.assertNotIn('через 2 хв', s['queue'][0]['text'])

    def test_no_ntp_still_reports_monotonic_duration(self):
        s = state()
        self.sample(s, False, 20, False)
        self.sample(s, True, 50, False)
        self.sample(s, True, 650, False)
        self.assertIn('за перевірками): 30 с', s['queue'][0]['text'])
        self.assertIn('немає надійного часу', s['queue'][0]['text'])

    def test_second_outage_during_ntp_wait_keeps_both(self):
        s = state(False)
        self.sample(s, False, 10, False)
        self.sample(s, True, 20, False)
        self.sample(s, False, 30, False)
        self.sample(s, True, 50, True)
        self.assertEqual(len(s['queue']), 2)
        self.assertIn('10 с', s['queue'][0]['text'])
        self.assertIn('20 с', s['queue'][1]['text'])

    def test_outage_spanning_reboot_uses_epoch(self):
        s = state()
        s['outage'] = dict(boot='old', mono=99999, ts=900)
        self.sample(s, True, 50)
        self.assertIn('за перевірками): 2 хв', s['queue'][0]['text'])

    def test_outage_spanning_reboot_without_clock_is_unknown(self):
        s = state(False)
        s['outage'] = dict(boot='old', mono=99999, ts=None)
        self.sample(s, True, 50)
        self.assertIn('тривалість невідома', s['queue'][0]['text'])

    def test_cross_boot_duration_after_delayed_ntp(self):
        s = state(False)
        s['outage'] = dict(boot='old', mono=99999, ts=900)
        self.sample(s, True, 50, False)
        self.sample(s, True, 150, True)
        self.assertIn('за перевірками): 2 хв', s['queue'][0]['text'])

    def test_more_than_six_hours_offline_is_not_discarded(self):
        s = state()
        self.sample(s, False, 10)
        self.sample(s, False, 30000)
        self.sample(s, True, 30010)
        self.assertIn('за перевірками): 8 год 20 хв', s['queue'][0]['text'])

    def test_multiple_boot_events_are_preserved(self):
        s = state()
        s['queue'].insert(0, dict(boot='older', prev=previous()))
        self.sample(s, True, 50)
        self.assertEqual(len(s['queue']), 2)
        self.assertNotIn('через 50 с', s['queue'][0]['text'])
        self.assertIn('через 50 с', s['queue'][1]['text'])

    def test_queue_survives_reload(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(m, 'HEARTBEAT_DIR', directory):
            s = state()
            self.sample(s, True, 50)
            m.save_state(s)
            self.assertEqual(m.load_state(), s)
            self.assertFalse((Path(directory) / 'monitor.json.tmp').exists())

    def test_corrupt_state_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(m, 'HEARTBEAT_DIR', directory):
            path = Path(directory) / 'monitor.json'
            path.write_text('{broken')
            with self.assertRaises(json.JSONDecodeError):
                m.load_state()
            self.assertEqual(path.read_text(), '{broken')

    def test_failed_delivery_survives_process_restart(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(m, 'HEARTBEAT_DIR', directory), \
                patch.object(m, 'boot_id', return_value='a'), \
                patch.object(m, 'previous_boot', return_value=previous()) as prev, \
                patch.object(m, 'have_internet', return_value=True), \
                patch.object(m, 'time_synced', return_value=True), \
                patch.object(m, 'uptime_sec', return_value=50), \
                patch.object(m, 'send_telegram', return_value=False), \
                patch.object(m.time, 'sleep', side_effect=InterruptedError):
            with self.assertRaises(InterruptedError):
                m.run_monitor()
            self.assertEqual(len(m.load_state()['queue']), 1)
            with patch.object(m, 'send_telegram', return_value=True):
                with self.assertRaises(InterruptedError):
                    m.run_monitor()
            self.assertEqual(m.load_state()['queue'], [])
            prev.assert_called_once()


class HistoryAndDelivery(unittest.TestCase):
    def test_short_unclean_boots_are_counted(self):
        short = dict(previous(clean=False), uptime=100)
        long = dict(previous(clean=False), uptime=1000)
        info = dict(bid='old', journal_ts=1000, uptime=100)
        with patch.object(m, 'boot_id', return_value='current'), \
                patch.object(m, 'boot_last_entry', side_effect=[info, info, info]), \
                patch.object(m, 'analyze_boot', side_effect=[short.copy(), short.copy(), long]):
            self.assertEqual(m.previous_boot()['flaps'], 2)

    def test_only_short_boots_keep_count(self):
        short = dict(previous(clean=False), uptime=100)
        info = dict(bid='old', journal_ts=1000, uptime=100)
        with patch.object(m, 'boot_id', return_value='current'), \
                patch.object(m, 'boot_last_entry', side_effect=[info, info, info, None]), \
                patch.object(m, 'analyze_boot', side_effect=[short.copy(), short.copy(), short.copy()]):
            self.assertEqual(m.previous_boot()['flaps'], 2)

    def test_telegram_requires_api_confirmation(self):
        response = Mock(status=200)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch.object(m, 'BOT_TOKEN', 'test'), patch.object(m.urllib.request, 'urlopen', return_value=response):
            for body, accepted in [(b'{"ok":true}', True), (b'{"ok":false}', False), (b'invalid', False)]:
                response.read.return_value = body
                self.assertEqual(m.send_telegram('test'), accepted)


if __name__ == '__main__':
    unittest.main()
