from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch, Mock
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from companion_core import Runtime, atomic_json
from startup import install_startup, remove_startup, task_request, installation_id, resume_temperature
from persistent_sensors import PersistentExporter, setup_persistent

class StartupChecks(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.wall=Path(self.temp.name)/'壁纸 with spaces'
        self.wall.mkdir()
        (self.wall/'project.json').write_text('{"type":"Web","file":"index.html"}')
        (self.wall/'index.html').write_text('<html>')
        self.data=self.wall/'telemetry-runtime'
        self.data.mkdir()

    def test_startup_stable_copy_repeated_enable_and_remove(self):
        executable=Path(self.temp.name)/'original.exe'
        executable.write_bytes(b'fixture exe; never executed')
        def runner(args,**kwargs):
            request=json.loads(Path(args[-1]).read_text(encoding='utf-8'))
            self.assertIn('--background',request['arguments'])
            self.assertIn(str(self.wall),request['arguments'])
            return Mock(returncode=0,stdout=json.dumps({'installed':request['action']=='install','userSid':'S-1-5-21-1'}))
        state=install_startup(self.data,self.wall,executable,runner)
        self.assertTrue(state['installed'])
        copied=Path(json.loads((self.data/'startup.json').read_text(encoding='utf-8'))['executable'])
        self.assertEqual(copied.read_bytes(),executable.read_bytes())
        stamp=copied.stat().st_mtime_ns
        install_startup(self.data,self.wall,executable,runner)
        self.assertEqual(stamp,copied.stat().st_mtime_ns)
        self.assertFalse(remove_startup(self.data,self.wall,runner)['installed'])

    def test_registration_failure_does_not_report_enabled(self):
        executable=Path(self.temp.name)/'fixture.exe'
        executable.write_bytes(b'fixture')
        def runner(*args,**kwargs): return Mock(returncode=1,stderr='fixture failure',stdout='')
        with self.assertRaises(RuntimeError): install_startup(self.data,self.wall,executable,runner)
        self.assertFalse((self.data/'startup.json').exists())

    def test_path_identifier_and_quoted_command(self):
        self.assertEqual(len(installation_id(self.data)),16)
        self.assertEqual(installation_id(self.data),installation_id(str(self.data).upper()))
        request=task_request(self.data,self.wall,'C:/test folder/collector.exe','install')
        self.assertIn('"'+str(self.wall)+'"',request['arguments'])

    def test_persistent_lease_stop_and_no_duplicate_resume(self):
        app=Runtime(self.data,Path(__file__).resolve().parents[1]/'configure.html',0)
        self.addCleanup(app.close)
        app.start()
        app.collector.configure({'temperatureSource':'alienware_wmi'})
        atomic_json(self.data/'persistent-sensors.json',{'installed':True})
        resume_temperature(app)
        manager=app.managed_exporter
        self.assertTrue(manager.heartbeat_path.exists())
        resume_temperature(app)
        self.assertIs(manager,app.managed_exporter)
        app.stop()
        self.assertTrue(manager.stop_path.exists())
        self.assertFalse(manager.heartbeat_thread.is_alive())

    def test_temperature_installer_cancel_preserves_current_sampling(self):
        app=Runtime(self.data,Path(__file__).resolve().parents[1]/'configure.html',0)
        self.addCleanup(app.close)
        app.start()
        def reject(*args): raise PermissionError('UAC fixture cancelled')
        with patch('managed_sensors.registered_export_paths',return_value=[]),patch('persistent_sensors.current_user_sid',return_value='S-1-5-21-1'):
            with self.assertRaises(PermissionError): setup_persistent(app,launcher=reject)
        self.assertIsNotNone(app.server)
        self.assertFalse((self.data/'persistent-sensors.json').exists())

    def test_background_resume_never_calls_elevated_launcher(self):
        app=Runtime(self.data,Path(__file__).resolve().parents[1]/'configure.html',0)
        self.addCleanup(app.close)
        app.start()
        app.collector.configure({'temperatureSource':'alienware_wmi'})
        with patch('persistent_sensors.ElevatedProcess',side_effect=AssertionError('No login UAC')):
            resume_temperature(app)
            self.assertIsNone(app.managed_exporter)

    def test_persistent_installer_receipt_and_remove(self):
        app=Runtime(self.data,Path(__file__).resolve().parents[1]/'configure.html',0)
        self.addCleanup(app.close)
        app.start()
        class Installed:
            def __init__(self,exe,args,directory):
                request=json.loads(Path(args[-1]).read_text(encoding='utf-8'))
                self.install=request['operation']=='install'
                atomic_json(self_root/'persistent-install-receipt.json',{'ok':True,'installed':self.install})
            def poll(self): return 0
            def close(self): pass
        self_root=self.data
        with patch('managed_sensors.registered_export_paths',return_value=[]),patch('persistent_sensors.current_user_sid',return_value='S-1-5-21-1'):
            setup_persistent(app,launcher=Installed)
            self.assertIsInstance(app.managed_exporter,PersistentExporter)
            self.assertTrue(app.managed_exporter.heartbeat_path.exists())
            setup_persistent(app,'remove',launcher=Installed)
            self.assertIsNone(app.managed_exporter)
            self.assertFalse(json.loads((self.data/'persistent-sensors.json').read_text())['installed'])

if __name__=='__main__': unittest.main()
