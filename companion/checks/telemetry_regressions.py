"""Isolated audit regressions. No production devices, tasks, audio or GUI.

python -B test_telemetry_regressions.py --source PATH --phase F1 --temp-root PATH
Omit --phase to run every applicable regression.
"""
import argparse
import ast
import ctypes
from ctypes import wintypes as W
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
import urllib.error
from unittest.mock import patch

parser = argparse.ArgumentParser()
parser.add_argument('--source', type=Path, required=True)
parser.add_argument('--phase', default='all')
parser.add_argument('--temp-root', type=Path, required=True)
args = parser.parse_args()
SOURCE = args.source.resolve()
TEMP_ROOT = args.temp_root.resolve()
TEMP_ROOT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(SOURCE))
import collector


class Fixtures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=TEMP_ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)


class F1LatestTemperature(Fixtures):
    def csv(self, content):
        path = self.root/'sensor.csv'
        path.write_text(content, encoding='utf-8-sig')
        return collector.HWiNFOLog(path)

    def test_latest_invalid_value_never_replays_old_temperature(self):
        for invalid in ('N/A', '', 'nan', 'inf', '151', '-31'):
            with self.subTest(invalid=invalid):
                source = self.csv('Date,Time,CPU Package [C]\n2026-11-13,10:00:00,73\n'
                                  f'2026-11-13,10:10:00,{invalid}\n')
                result = source.read()
                self.assertIsNone(result['valueC'])
                self.assertEqual(result['status'], 'offline')

    def test_incomplete_latest_row_never_replays_or_reads_partial_value(self):
        for tail in ('2026-11-13,10:10:00\n', '2026-11-13,10:10:00,8', '2026-11-13,10:10:00,"81\n'):
            with self.subTest(tail=tail):
                source = self.csv('Date,Time,CPU Package [C]\n2026-11-13,10:00:00,73\n'+tail)
                self.assertIsNone(source.read()['valueC'])

    def test_valid_latest_row_and_blank_trailing_lines(self):
        source = self.csv('Date,CPU Package [C]\n2026-11-13,73\n2026-11-13,81\n\n')
        self.assertEqual(source.read()['valueC'], 81)

    def test_stopped_file_clears_temperature(self):
        source = self.csv('Date,CPU Package [C]\n2026-11-13,73\n')
        old = time.time()-10
        os.utime(source.path, (old, old))
        self.assertEqual(source.read()['status'], 'stale')
        self.assertIsNone(source.read()['valueC'])

    def test_semicolon_decimal_comma_and_duplicate_sensor_selection(self):
        source = self.csv('Date;CPU Package [C];CPU Package [C]\n2026-11-13;55,5;81,5\n')
        sensors = source.read()['availableSensors']
        self.assertEqual(len(sensors), 2)
        source.selected = sensors[1]
        self.assertEqual(source.read()['valueC'], 81.5)

    def test_missing_or_changed_header_stays_offline(self):
        source = self.csv('Date,CPU Package [C]\n2026-11-13,73\n')
        source.selected = 'removed-column'
        self.assertIsNone(source.read()['valueC'])
        source.path.unlink()
        self.assertEqual(source.read()['status'], 'offline')

    def test_bounded_tail_discards_initial_partial_csv_row(self):
        source = self.csv('Date,CPU Package [C]\n'+('2026-11-13,"73"\n'*10000)+'2026-11-13,81\n')
        self.assertEqual(source.read()['valueC'], 81)


class FakeCollector:
    def __init__(self, *args): self.started = False
    def start(self): self.started = True
    def close(self): self.started = False
    def read(self): return {'schemaVersion':1, 'status':'fake', 'history':[]}
    def read_audio(self): return {'status':'offline'}


def inert_collector(root):
    audio_owner = collector if hasattr(collector, 'AudioProvider') else collector._audio_module
    with patch.object(collector, 'SystemCache'), patch.object(collector, 'NvidiaProvider'), patch.object(audio_owner, 'AudioProvider'):
        return collector.Collector(root/'sensor.csv', root/'user-settings.json')


def lock_state_file(path):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, ctypes.c_void_p, W.DWORD, W.DWORD, W.HANDLE]
    kernel.CreateFileW.restype = W.HANDLE
    kernel.CloseHandle.argtypes = [W.HANDLE]
    handle = kernel.CreateFileW(str(path), 0x80000000, 1, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    return kernel, handle


@unittest.skipUnless((SOURCE/'companion_core.py').exists(), 'No standalone Runtime in Python service')
class F2RuntimeRecovery(Fixtures):
    def setUp(self):
        super().setUp()
        import companion_core
        import portable_server
        self.core = companion_core
        self.fake = patch.object(portable_server, 'Collector', FakeCollector)
        self.fake.start()
        self.addCleanup(self.fake.stop)
        self.app = companion_core.Runtime(self.root, SOURCE/'configure.html', 0)
        self.addCleanup(self.app.close)

    def assert_online(self):
        with urllib.request.urlopen(f'http://127.0.0.1:{self.app.port}/api/v1/snapshot', timeout=2) as response:
            self.assertEqual(response.status, 200)
        self.assertTrue(self.app.collector.started)
        self.assertTrue(self.app.thread.is_alive())

    def test_normal_stop_is_idempotent_and_restart_works(self):
        self.app.start()
        self.assert_online()
        previous = self.app.thread
        self.app.stop()
        self.app.stop()
        self.assertFalse(previous.is_alive())
        self.app.start()
        self.assert_online()

    def test_locked_state_file_does_not_block_stop_or_restart(self):
        self.app.start()
        previous = self.app.thread
        kernel, handle = lock_state_file(self.root/'runtime-state.json')
        try:
            with self.assertLogs('mornye', level='WARNING'):
                self.app.stop()
                self.assertIsNone(self.app.server)
                self.assertIsNone(self.app.collector)
                self.assertIsNone(self.app.thread)
                self.assertFalse(previous.is_alive())
                self.app.start()
            self.assert_online()
        finally:
            kernel.CloseHandle(handle)
        self.app.stop()
        self.app.start()
        self.assert_online()

    def test_state_write_failure_during_first_start_is_only_diagnostic(self):
        real_write = self.core.atomic_json
        def blocked(path, value):
            if Path(path).name == 'runtime-state.json':
                raise PermissionError('isolated fixture')
            return real_write(path, value)
        with patch.object(self.core, 'atomic_json', side_effect=blocked), self.assertLogs('mornye', level='WARNING'):
            self.app.start()
            self.assert_online()
            self.app.stop()
        self.assertIsNone(self.app.server)


class F3ConfigurationTransaction(Fixtures):
    def setUp(self):
        super().setUp()
        self.engine = inert_collector(self.root)
        self.addCleanup(self.engine.close)
        self.engine.selected_adapter = 'old'
        self.engine.network_prev = (1, (2, 3))
        self.engine.network_base = (2, 3)
        self.engine.history.append({'cpuTemperature':73, 'cpuPackageTemperature':73,
            'networkDownloadBytesPerSecond':10, 'networkUploadBytesPerSecond':20})
        self.engine.snapshot = {'cpu':{'temperatureSource':'hwinfo_csv', 'temperatureC':73,
            'packageTemperatureC':73, 'temperatureProvider':{'status':'online', 'valueC':73},
            'temperatureSources':{'hwinfo_csv':{'availableSensors':['CSV1','CSV2']}}},
            'alienware':{'sensors':[{'key':'0x01', 'valueC':65}]}, 'network':{'adapter':'old'},
            'history':list(self.engine.history)}
        self.engine.configure({})

    def state(self):
        e = self.engine
        return json.dumps({'adapter':e.selected_adapter, 'manual':e.manual_adapter,
            'source':e.temperature_source, 'sensor':e.sensor.selected, 'awcc':e.awcc_temperature_sensor,
            'previous':e.network_prev, 'base':e.network_base, 'session':e.network_session,
            'history':list(e.history), 'snapshot':e.snapshot}, sort_keys=True)

    def test_invalid_later_field_changes_neither_memory_nor_saved_file(self):
        before = self.state()
        saved = self.engine.settings_path.read_bytes()
        with self.assertRaises(ValueError):
            self.engine.configure({'temperatureSource':'alienware_wmi', 'awccTemperatureSensor':'0xFF'})
        self.assertEqual(self.state(), before)
        self.assertEqual(self.engine.settings_path.read_bytes(), saved)

    def test_invalid_combined_request_preserves_network_session_and_history(self):
        before = self.state()
        with patch.object(collector.psutil, 'net_io_counters', return_value={'old':None,'new':None}):
            with self.assertRaises(ValueError):
                self.engine.configure({'adapter':'new','temperatureSensor':'CSV2','temperatureSource':'invalid'})
        self.assertEqual(self.state(), before)

    def test_locked_settings_file_does_not_apply_unsaved_values(self):
        before = self.state()
        saved = self.engine.settings_path.read_bytes()
        kernel, handle = lock_state_file(self.engine.settings_path)
        try:
            with self.assertRaises(PermissionError):
                self.engine.configure({'temperatureSource':'alienware_wmi','awccTemperatureSensor':'0x01'})
        finally:
            kernel.CloseHandle(handle)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.engine.settings_path.read_bytes(), saved)
        self.engine.configure({'temperatureSource':'alienware_wmi','awccTemperatureSensor':'0x01'})
        self.assertEqual(self.engine.temperature_source, 'alienware_wmi')

    def test_valid_combined_temperature_request_persists_and_reloads(self):
        self.engine.configure({'temperatureSource':'alienware_wmi','awccTemperatureSensor':'0x01'})
        restored = inert_collector(self.root)
        self.addCleanup(restored.close)
        self.assertEqual(restored.temperature_source, 'alienware_wmi')
        self.assertEqual(restored.awcc_temperature_sensor, '0x01')
        self.assertIsNone(self.engine.history[0]['cpuTemperature'])
        self.assertEqual(self.engine.history[0]['networkDownloadBytesPerSecond'], 10)
        self.assertIsNone(self.engine.snapshot['cpu']['temperatureC'])

    def test_valid_adapter_request_resets_only_network_state(self):
        with patch.object(collector.psutil, 'net_io_counters', return_value={'old':None,'new':None}):
            self.engine.configure({'adapter':'new'})
        self.assertEqual(self.engine.selected_adapter, 'new')
        self.assertTrue(self.engine.manual_adapter)
        self.assertIsNone(self.engine.network_prev)
        self.assertIsNone(self.engine.history[0]['networkDownloadBytesPerSecond'])
        self.assertEqual(self.engine.history[0]['cpuTemperature'], 73)

    def test_in_memory_only_configuration_still_works(self):
        self.engine.settings_path = None
        self.engine.configure({'temperatureSensor':'CSV2'})
        self.assertEqual(self.engine.sensor.selected, 'CSV2')

    def test_http_error_responses_leave_current_configuration_unchanged(self):
        if (SOURCE/'portable_server.py').exists():
            import portable_server
            with patch.object(portable_server, 'Collector', return_value=self.engine):
                server, _ = portable_server.create_server(self.root, 0, 'fixture-key', SOURCE/'configure.html', self.root/'sensor.csv')
        else:
            # Execute the real legacy handler classes without the module's
            # production bootstrap, settings writes or hardware thread startup.
            from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
            from urllib.parse import urlparse
            import hmac
            path = SOURCE/'serve.py'
            tree = ast.parse(path.read_text(encoding='utf-8'))
            classes = ast.Module(body=[n for n in tree.body if isinstance(n, ast.ClassDef)], type_ignores=[])
            namespace = dict(BaseHTTPRequestHandler=BaseHTTPRequestHandler, ThreadingHTTPServer=ThreadingHTTPServer,
                urlparse=urlparse, hmac=hmac, json=json, sys=sys, collector=self.engine, READ_KEY='fixture-key',
                READ_PATHS={'/api/v1/snapshot','/api/v1/info','/api/v1/audio'}, FILE_ORIGINS={'null','file://'})
            exec(compile(classes, str(path), 'exec'), namespace)
            server = namespace['LocalTelemetryServer'](('127.0.0.1',0),namespace['Handler'])
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        before = self.state()
        def post(sensor, status):
            request = urllib.request.Request(f'http://127.0.0.1:{server.server_port}/api/v1/config',
                data=json.dumps({'temperatureSource':'alienware_wmi','awccTemperatureSensor':sensor}).encode(),
                headers={'Content-Type':'application/json'})
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(request, timeout=2)
            self.assertEqual(caught.exception.code, status)
            self.assertIn('error', json.load(caught.exception))
            self.assertEqual(self.state(), before)
        try:
            post('0xFF', 400)
            kernel, handle = lock_state_file(self.engine.settings_path)
            try:
                post('0x01', 503)
            finally:
                kernel.CloseHandle(handle)
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()


@unittest.skipUnless((SOURCE/'startup-task.ps1').exists(), 'Login installation belongs to candidate only')
class F4StartupInfo(Fixtures):
    def setUp(self):
        super().setUp()
        import companion_core
        import portable_server
        import startup
        self.startup = startup
        self.fake = patch.object(portable_server, 'Collector', FakeCollector)
        self.fake.start()
        self.addCleanup(self.fake.stop)
        self.app = companion_core.Runtime(self.root/'data', SOURCE/'configure.html', 0)
        self.addCleanup(self.app.close)
        self.app.start()

    def info(self):
        with urllib.request.urlopen(f'http://127.0.0.1:{self.app.port}/api/v1/info', timeout=2) as response:
            self.assertEqual(response.status, 200)
            return json.load(response)['startup']['installed']

    def test_install_and_remove_match_api_status(self):
        from types import SimpleNamespace
        wall = self.root/'wall'
        wall.mkdir()
        (wall/'project.json').write_text('{"type":"web","file":"index.html"}')
        (wall/'index.html').write_text('<html></html>')
        executable = self.root/'fixture.exe'
        executable.write_bytes(b'fixture, never executed')
        def runner(command, **kwargs):
            request = json.loads(Path(command[-1]).read_text(encoding='utf-8'))
            return SimpleNamespace(returncode=0, stdout=json.dumps({'installed':request['action']=='install'}), stderr='')
        self.startup.install_startup(self.app.data_root, wall, executable=executable, runner=runner)
        self.assertTrue(self.startup.startup_state(self.app.data_root)['installed'])
        self.assertTrue(self.info())
        self.startup.remove_startup(self.app.data_root, wall, runner=runner)
        self.assertFalse(self.info())

    def test_missing_invalid_or_non_object_status_stays_available_and_disabled(self):
        self.assertFalse(self.info())
        for content in ('{', '[]', 'null', '{}', '{"installed":"yes"}'):
            with self.subTest(content=content):
                (self.app.data_root/'startup.json').write_text(content, encoding='utf-8')
                self.assertFalse(self.info())

    def test_legacy_status_file_cannot_override_current_installation(self):
        records = self.app.data_root/'records'
        records.mkdir()
        (records/'collector-startup.json').write_text('{"installed":true}')
        self.assertFalse(self.info())
        (self.app.data_root/'startup.json').write_text('{"installed":true}')
        (records/'collector-startup.json').write_text('{"installed":false}')
        self.assertTrue(self.info())


if __name__ == '__main__':
    classes = [F1LatestTemperature, F2RuntimeRecovery, F3ConfigurationTransaction, F4StartupInfo]
    suite = unittest.TestSuite()
    for cls in classes:
        if args.phase == 'all' or cls.__name__.startswith(args.phase):
            suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(cls))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
