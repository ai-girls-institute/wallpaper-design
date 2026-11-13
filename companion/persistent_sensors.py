from pathlib import Path
import hashlib
import json
import os
import threading
import time
from companion_core import atomic_json
from managed_sensors import ElevatedProcess
from startup import installation_id, powershell, current_user_sid
from persistent_integrity import INSTALLER_SHA256

def persistent_state(data_root):
    try: return json.loads((Path(data_root)/'persistent-sensors.json').read_text(encoding='utf-8'))
    except (OSError, ValueError): return {'installed':False}

def setup_persistent(app, operation='install', launcher=ElevatedProcess):
    # A pre-existing independent installation already provides login telemetry.
    if operation=='install':
        from managed_sensors import registered_export_paths
        from collector import AlienwareExport
        for candidate in registered_export_paths():
            try: data=AlienwareExport(candidate).read()
            except OSError: continue
            if data['status'] in ('online','partial') and data['sensors']:
                app.sensor_export(candidate)
                return {'mode':'external','message':'已接入已有温度后台任务；无需重复安装。'}
    resource=Path(__file__).with_name('persistent-install.ps1')
    if hashlib.sha256(resource.read_bytes()).hexdigest()!=INSTALLER_SHA256:
        raise ValueError('温度启动安装器内容不匹配，未请求管理员授权。')
    root=app.data_root
    request=root/'persistent-request.json'
    receipt=root/'persistent-install-receipt.json'
    receipt.unlink(missing_ok=True)
    atomic_json(request,{'operation':operation,'dataRoot':str(root.resolve()),
        'installationId':installation_id(root),'userSid':current_user_sid()})
    child=launcher(powershell(),['-NoProfile','-NonInteractive','-WindowStyle','Hidden','-ExecutionPolicy','Bypass',
        '-File',str(resource),'-RequestPath',str(request)],resource.parent)
    try:
        deadline=time.monotonic()+45
        while child.poll() is None and time.monotonic()<deadline: time.sleep(.1)
        code=child.poll()
        if code is None: raise RuntimeError('温度启动设置仍在执行，请稍后查看；未报告完成。')
        if not receipt.is_file(): raise RuntimeError(f'温度启动设置失败（代码 {code}）；其他采集继续。')
        result=json.loads(receipt.read_text(encoding='utf-8-sig'))
        if code!=0 or not result.get('ok'): raise RuntimeError(result.get('message','温度启动设置失败。'))
    finally: child.close()
    atomic_json(root/'persistent-sensors.json',result)
    if app.managed_exporter:
        app.managed_exporter.stop()
        app.managed_exporter=None
    if operation=='install':
        manager=PersistentExporter(root)
        manager.start()
        app.managed_exporter=manager
        with app.collector.lock:
            app.collector.alienware.path=manager.export_path
            app.collector.configure({'temperatureSource':'alienware_wmi'})
    return {'mode':'persistent','message':'温度登录启动已安装。' if operation=='install' else '本次安装的温度登录任务已取消。'}

class PersistentExporter:
    """Own control files lease the protected scheduled helper; no per-login UAC."""
    def __init__(self,data_root):
        root=Path(data_root)
        self.export_path=root/'persistent-alienware/alienware-live.json'
        self.control_root=root/'persistent-alienware-control'
        self.stop_path=self.control_root/'stop.signal'
        self.heartbeat_path=self.control_root/'owner.heartbeat'
        self.finished=threading.Event()
        self.heartbeat_thread=None
        self.process=None

    def start(self):
        self.control_root.mkdir(parents=True,exist_ok=True)
        self.stop_path.unlink(missing_ok=True)
        self.heartbeat_path.touch()
        self.finished.clear()
        self.process=self  # Runtime's owned-manager sentinel, no process handle
        self.heartbeat_thread=threading.Thread(target=self.keep_lease,daemon=True)
        self.heartbeat_thread.start()
        return self.export_path

    def poll(self): return None if self.process else 0

    def keep_lease(self):
        while not self.finished.wait(1):
            try: self.heartbeat_path.touch()
            except OSError: return

    def stop(self):
        self.finished.set()
        if self.heartbeat_thread: self.heartbeat_thread.join(timeout=2)
        try: self.stop_path.write_text('stop',encoding='ascii')
        except OSError: pass
        self.process=None
