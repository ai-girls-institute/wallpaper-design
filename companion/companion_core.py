from pathlib import Path
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
import ctypes
from ctypes import wintypes
import hashlib
import json
import logging
import math
import os
import secrets
import shutil
import threading
import time

APP_VERSION = '0.1.4-beta.1'
WORKSHOP_ID = '3817553402'

def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)

def default_data_root():
    return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'MornyeObservatory/Telemetry'

def load_key(data_root):
    path = Path(data_root) / 'read-access.json'
    if path.exists():
        key = json.loads(path.read_text(encoding='utf-8')).get('key')
        if not isinstance(key, str) or len(key) < 32 or not key.isascii():
            raise ValueError('读取密钥文件无效，请保留该文件并联系作者；程序未覆盖它。')
        return key
    key = secrets.token_urlsafe(32)
    atomic_json(path, {'key':key})
    return key

def validate_wallpaper(directory):
    directory = Path(directory).expanduser().resolve()
    definition = json.loads((directory / 'project.json').read_text(encoding='utf-8-sig'))
    kind = definition.get('type')
    if not isinstance(kind, str) or kind.strip().casefold() != 'web':
        raise ValueError('请选择网页壁纸目录，包含 project.json 和 index.html。')
    entry = (directory / definition.get('file', 'index.html')).resolve()
    if not entry.is_relative_to(directory) or not entry.is_file():
        raise ValueError('目录内找不到壁纸网页入口，请选择实际订阅下载目录。')
    return directory

def wallpaper_data_root(directory):
    return validate_wallpaper(directory) / 'telemetry-runtime'

def bind_wallpaper(data_root, directory, key, port):
    directory = validate_wallpaper(directory)
    destination = directory / 'telemetry-config.local.js'
    content = 'window.WALLPAPER_TELEMETRY = ' + json.dumps(
        {'base':f'http://127.0.0.1:{port}', 'key':key}, ensure_ascii=False, separators=(',', ':')) + ';\n'
    if destination.exists():
        previous = destination.read_bytes()
        if previous != content.encode('utf-8'):
            backup = Path(data_root) / 'backups' / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.js')
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(destination, backup)
    temporary = destination.with_name(destination.name + '.tmp')
    try:
        temporary.write_text(content, encoding='utf-8', newline='\n')
        temporary.replace(destination)
    except PermissionError as error:
        raise PermissionError('壁纸目录不可写，请确认目录正确并具有写入权限。') from error
    atomic_json(Path(data_root) / 'binding.json', {'wallpaperDirectory':str(directory), 'port':port,
                                                'updatedAt':datetime.now(timezone.utc).isoformat()})
    return destination

def discover_wallpapers(steam_roots=None):
    import re
    if steam_roots is None:
        import winreg
        steam_roots = []
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam') as key:
                steam_roots.append(Path(winreg.QueryValueEx(key, 'SteamPath')[0]))
        except OSError:
            pass
        steam_roots.append(Path(os.environ.get('ProgramFiles(x86)', 'C:/Program Files (x86)')) / 'Steam')
    libraries = list(map(Path, steam_roots))
    for steam_root in list(libraries):
        vdf = steam_root / 'steamapps/libraryfolders.vdf'
        if vdf.is_file():
            for library in re.findall(r'"path"\s+"([^"]+)"', vdf.read_text(encoding='utf-8-sig')):
                libraries.append(Path(library.replace('\\\\', '\\')))
    found = []
    for library in libraries:
        target = library / f'steamapps/workshop/content/431960/{WORKSHOP_ID}'
        try:
            directory = validate_wallpaper(target)
        except (OSError, ValueError, TypeError):
            continue
        if directory not in found:
            found.append(directory)
    return found

class SingleInstance:
    def __init__(self, data_root):
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        self.kernel.CreateMutexW.restype = wintypes.HANDLE
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        name = 'Local\\MornyeStandalone-' + hashlib.sha256(str(Path(data_root).resolve()).lower().encode()).hexdigest()[:16]
        self.handle = self.kernel.CreateMutexW(None, False, name)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self.already_running = ctypes.get_last_error() == 183

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None

class Runtime:
    def __init__(self, data_root, page_path, port=None):
        self.data_root = Path(data_root).resolve()
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.page_path = Path(page_path)
        self.requested_port = port
        self.server = self.collector = self.thread = None
        self.managed_exporter = None
        self.key = load_key(self.data_root)
        self.logger = logging.getLogger('mornye')
        self.logger.setLevel(logging.INFO)
        self.log_handler = RotatingFileHandler(self.data_root / 'collector.log', maxBytes=2*1024*1024,
                                               backupCount=1, encoding='utf-8')
        self.logger.addHandler(self.log_handler)

    @property
    def port(self):
        return self.server.server_port if self.server else None

    @property
    def url(self):
        return f'http://127.0.0.1:{self.port}/configure.html' if self.server else None

    def state(self, status):
        try:
            atomic_json(self.data_root / 'runtime-state.json', {'version':APP_VERSION, 'status':status,
                        'processId':os.getpid(), 'port':self.port, 'url':self.url,
                        'updatedAt':datetime.now(timezone.utc).isoformat()})
        except OSError as error:
            # Diagnostic persistence must not control the collector lifecycle.
            self.logger.warning('Runtime state write unavailable: %s', error)

    def start(self):
        if self.server:
            return
        from portable_server import create_server
        settings_path = self.data_root / 'user-settings.json'
        sensor_log = self.data_root / 'sensor-input/HWiNFO.csv'
        if settings_path.exists():
            sensor_log = Path(json.loads(settings_path.read_text(encoding='utf-8')).get('sensorLogPath', sensor_log))
        (self.data_root / 'sensor-input').mkdir(exist_ok=True)
        ports = [self.requested_port] if self.requested_port is not None else self.preferred_ports()
        last_error = None
        for port in ports:
            try:
                self.server, self.collector = create_server(self.data_root, port, self.key, self.page_path, sensor_log)
                break
            except OSError as error:
                if error.errno not in (98, 10048, 10013) and getattr(error, 'winerror', None) not in (10048, 10013):
                    raise
                last_error = error
        if not self.server:
            raise OSError('本地端口均被占用；未停止任何其他程序。') from last_error
        try:
            self.collector.start()
            self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self.thread.start()
            self.state('running')
            self.logger.info('Collector started on loopback port %s', self.port)
        except Exception:
            self.server.server_close()
            self.collector.close()
            self.server = self.collector = None
            raise

    def preferred_ports(self):
        preferred = 64582
        binding = self.data_root / 'binding.json'
        if binding.exists():
            candidate = json.loads(binding.read_text(encoding='utf-8')).get('port')
            if isinstance(candidate, int) and 64582 <= candidate <= 64592:
                preferred = candidate
        return [preferred] + [port for port in range(64582, 64593) if port != preferred]

    def bind(self, directory):
        directory = validate_wallpaper(directory)
        self.start()
        return bind_wallpaper(self.data_root, directory, self.key, self.port)

    def sensor_log(self, path):
        self.start()
        target = Path(path).resolve()
        if not target.is_file():
            raise ValueError('请选择存在的 CSV 日志文件。')
        with self.collector.lock:
            self.collector.sensor.path = target
            self.collector.sensor.selected = None
            self.collector.configure({'temperatureSource':'hwinfo_csv'})

    def sensor_export(self, path):
        target = Path(path).resolve()
        data = json.loads(target.read_text(encoding='utf-8-sig'))
        if not isinstance(data, dict):
            raise ValueError('请选择 Alienware 温度导出 JSON 对象。')
        stamp = data.get('timestampMs')
        if (data.get('schemaVersion') != 1 or isinstance(stamp, bool) or not isinstance(stamp, (int,float))
                or not math.isfinite(stamp)
                or not isinstance(data.get('sensors', []), list)):
            raise ValueError('请选择本项目 Alienware 温度导出文件（schemaVersion 1，含 timestampMs）。')
        self.start()
        with self.collector.lock:
            self.collector.alienware.path = target
            self.collector.configure({'temperatureSource':'alienware_wmi'})
        return self.collector.alienware.read()

    def start_alienware(self, manager_factory=None, installation_path=None):
        from collector import AlienwareExport
        from managed_sensors import ManagedExporter, registered_export_paths
        self.start()
        if self.managed_exporter and self.managed_exporter.process and self.managed_exporter.poll() is None:
            return {'mode':'managed', 'message':'本程序的Alienware温度辅助采集已运行。'}
        for candidate in registered_export_paths(self.collector.alienware.path, installation_path):
            try:
                data = AlienwareExport(candidate).read()
            except OSError:
                # An old installation may belong to another Windows user.
                # Inaccessible existing data must not block the new-user path.
                continue
            if data['status'] in ('online','partial') and data['sensors']:
                self.sensor_export(candidate)
                return {'mode':'existing','message':'已自动接入现有Alienware温度采集，无需重复启动。'}
        if self.managed_exporter:
            self.managed_exporter.stop()
        factory = manager_factory or ManagedExporter
        manager = factory(self.data_root, Path(__file__).with_name('managed-awcc.ps1'))
        export = manager.start()
        self.managed_exporter = manager
        with self.collector.lock:
            self.collector.alienware.path = export
            self.collector.configure({'temperatureSource':'alienware_wmi'})
        return {'mode':'managed','message':'已授权启动Alienware温度辅助采集，正在读取传感器。'}

    def alienware_state(self):
        if not self.collector:
            return None
        data = self.collector.alienware.read()
        if self.managed_exporter:
            code = self.managed_exporter.poll()
            if code == 17:
                data['message'] = '另一个Alienware读取器正在运行，未启动重复采集。'
            elif code is not None and not data['sensors']:
                data['message'] = f'温度辅助进程已结束（代码{code}）。'+data.get('message','')
        return data

    def stop(self):
        if self.managed_exporter:
            self.managed_exporter.stop()
            self.managed_exporter = None
        if self.server:
            self.server.shutdown()
            self.thread.join(timeout=3)
            self.server.server_close()
            self.collector.close()
            try:
                self.state('stopped')
            finally:
                self.server = self.collector = self.thread = None
            self.logger.info('Collector stopped')

    def close(self):
        try:
            self.stop()
        finally:
            self.logger.removeHandler(self.log_handler)
            self.log_handler.close()
