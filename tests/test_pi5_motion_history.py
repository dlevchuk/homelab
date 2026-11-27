"""Motion history survives service restarts and stays bounded."""
from contextlib import closing
import ast
from pathlib import Path
import sqlite3
import time
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'ansible/raspberry/roles/pi5_dashboard/files/pi5_dashboard.py'


class MotionHistory(unittest.TestCase):
    def test_persistence_order_and_retention(self):
        tree = ast.parse(SCRIPT.read_text())
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'motion_history')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state/motion.sqlite3'
            namespace = dict(sqlite3=sqlite3, closing=closing, HISTORY_DB=path)
            code = compile(ast.Module(body=[function], type_ignores=[]), str(SCRIPT), 'exec')
            exec(code, namespace)
            history = namespace['motion_history']
            self.assertEqual(history(), [])
            history(100)
            exec(code, namespace)
            self.assertEqual(namespace['motion_history'](), [100])
            with closing(sqlite3.connect(path)) as db, db:
                db.executemany('INSERT INTO motion(ts) VALUES (?)', [(n,) for n in range(1000)])
            recent = history(2000)
            self.assertEqual(recent[:2], [2000, 999])
            self.assertEqual(len(recent), 20)
            with closing(sqlite3.connect(path)) as db, db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM motion').fetchone()[0], 1000)

    def test_period_counts_buckets_and_invalid_filter(self):
        tree = ast.parse(SCRIPT.read_text())
        functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ('motion_history', 'motion_report')]
        with tempfile.TemporaryDirectory() as directory:
            namespace = dict(sqlite3=sqlite3, closing=closing, time=time, HISTORY_DB=Path(directory) / 'motion.sqlite3')
            exec(compile(ast.Module(body=functions, type_ignores=[]), str(SCRIPT), 'exec'), namespace)
            now = time.mktime((2026, 11, 27, 12, 0, 0, 0, 0, -1))
            for timestamp in (now - 7200, now - 3600, now - 20, now + 1):
                namespace['motion_history'](timestamp)
            report = namespace['motion_report']('hour', now)
            self.assertEqual(report['count'], 2)
            self.assertEqual(sum(report['buckets']), 2)
            self.assertEqual(report['events'], [now - 20, now - 3600])
            self.assertEqual(namespace['motion_report']('today', now)['count'], 3)
            with self.assertRaises(ValueError):
                namespace['motion_report']('invalid', now)

    def test_auto_fan_uses_os_trip_points(self):
        source = SCRIPT.parents[1] / 'templates/pi5-fan-control.j2'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fan = root / 'cooling_device0'; fan.mkdir()
            zone = root / 'thermal_zone0'; zone.mkdir()
            for name, value in {'type':'pwm-fan','max_state':'4','cur_state':'4'}.items():
                (fan / name).write_text(value)
            for name, value in {'type':'cpu-thermal','policy':'step_wise','temp':'56000'}.items():
                (zone / name).write_text(value)
            for index, temperature in enumerate((45000,55000,65000,75000)):
                (zone / f'trip_point_{index}_temp').write_text(str(temperature))
                (zone / f'trip_point_{index}_type').write_text('active')
            script = root / 'control.sh'
            script.write_text(source.read_text().replace('/sys/class/thermal', str(root)))
            subprocess.run(['sh',str(script),'auto'],check=True)
            self.assertEqual((fan / 'cur_state').read_text().strip(), '2')
            (zone / 'temp').write_text('38000')
            subprocess.run(['sh',str(script),'auto'],check=True)
            self.assertEqual((fan / 'cur_state').read_text().strip(), '0')
            self.assertNotEqual(subprocess.run(['sh',str(script),'invalid']).returncode, 0)
