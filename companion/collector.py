"""Read-only, local Windows / NVIDIA telemetry collector. No test or audit mode."""
from __future__ import annotations
from collections import deque
from datetime import datetime
from pathlib import Path
import csv
import ctypes as C
from ctypes import wintypes as W
import io
import json
import math
import os
import re
import threading
import time
import uuid
import psutil
from disabled_audio import AudioProvider

INTERVAL = 0.25
FRESH_SECONDS = 4.0

def finite(value):
    return value if isinstance(value, (int, float)) and math.isfinite(value) else None

def iso_now():
    return datetime.now().astimezone().isoformat(timespec='milliseconds')

class PerformanceInfo(C.Structure):
    _fields_ = [('cb', W.DWORD)] + [(name, C.c_size_t) for name in (
        'CommitTotal', 'CommitLimit', 'CommitPeak', 'PhysicalTotal',
        'PhysicalAvailable', 'SystemCache', 'KernelTotal', 'KernelPaged',
        'KernelNonpaged', 'PageSize')] + [('HandleCount', W.DWORD),
        ('ProcessCount', W.DWORD), ('ThreadCount', W.DWORD)]

class SystemCache:
    def __init__(self):
        self.call = C.WinDLL('psapi', use_last_error=True).GetPerformanceInfo
        self.call.argtypes = [C.POINTER(PerformanceInfo), W.DWORD]
        self.call.restype = W.BOOL

    def read(self):
        info = PerformanceInfo()
        info.cb = C.sizeof(info)
        return int(info.SystemCache * info.PageSize) if self.call(C.byref(info), info.cb) else None

class Utilization(C.Structure):
    _fields_ = [('gpu', C.c_uint), ('memory', C.c_uint)]

class GPUMemory(C.Structure):
    _fields_ = [('total', C.c_ulonglong), ('free', C.c_ulonglong), ('used', C.c_ulonglong)]

class NvidiaProvider:
    """NVML calls only retrieve metrics; unsupported individual fields stay null."""
    def __init__(self, index=0):
        self.index = index
        self.lib = None
        self.handle = C.c_void_p()

    def bind(self, name, args):
        fn = getattr(self.lib, name)
        fn.argtypes = args
        fn.restype = C.c_int
        return fn

    def open(self):
        paths = [Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/nvml.dll',
                 Path(os.environ.get('ProgramW6432', 'C:/Program Files')) / 'NVIDIA Corporation/NVSMI/nvml.dll']
        path = next((p for p in paths if p.is_file()), None)
        if path is None: raise RuntimeError('NVIDIA 驱动接口未找到')
        self.lib = C.CDLL(str(path))
        initialized = False
        try:
            if self.bind('nvmlInit_v2', [])() != 0: raise RuntimeError('NVIDIA 驱动接口未就绪')
            initialized = True
            if self.bind('nvmlDeviceGetHandleByIndex_v2', [C.c_uint, C.POINTER(C.c_void_p)])(self.index, C.byref(self.handle)) != 0:
                raise RuntimeError('NVIDIA GPU 不可用')
            self.util = self.bind('nvmlDeviceGetUtilizationRates', [C.c_void_p, C.POINTER(Utilization)])
            self.mem = self.bind('nvmlDeviceGetMemoryInfo', [C.c_void_p, C.POINTER(GPUMemory)])
            self.temp = self.bind('nvmlDeviceGetTemperature', [C.c_void_p, C.c_uint, C.POINTER(C.c_uint)])
            self.power = self.bind('nvmlDeviceGetPowerUsage', [C.c_void_p, C.POINTER(C.c_uint)])
            self.name = self.bind('nvmlDeviceGetName', [C.c_void_p, C.c_void_p, C.c_uint])
        except Exception:
            if initialized: self.lib.nvmlShutdown()
            self.lib = None
            raise

    def read(self):
        if self.lib is None: self.open()
        util, memory, temperature, power = Utilization(), GPUMemory(), C.c_uint(), C.c_uint()
        name = C.create_string_buffer(128)
        data = {'index': self.index, 'source': 'NVIDIA NVML', 'name': None,
                'loadPercent': None, 'temperatureC': None, 'powerWatts': None,
                'memoryUsedBytes': None, 'memoryTotalBytes': None, 'memoryUsedPercent': None}
        if self.name(self.handle, name, len(name)) == 0:
            data['name'] = name.value.decode('utf-8', errors='replace')
        if self.util(self.handle, C.byref(util)) == 0: data['loadPercent'] = int(util.gpu)
        if self.mem(self.handle, C.byref(memory)) == 0:
            data.update(memoryUsedBytes=int(memory.used), memoryTotalBytes=int(memory.total),
                        memoryUsedPercent=memory.used / memory.total * 100 if memory.total else None)
        if self.temp(self.handle, 0, C.byref(temperature)) == 0: data['temperatureC'] = int(temperature.value)
        if self.power(self.handle, C.byref(power)) == 0: data['powerWatts'] = power.value / 1000.0
        values = [data[k] for k in ['loadPercent', 'temperatureC', 'powerWatts', 'memoryUsedBytes']]
        data['status'] = 'online' if all(v is not None for v in values) else 'partial' if any(v is not None for v in values) else 'offline'
        if data['status'] == 'offline': self.close()
        return data

    def close(self):
        if self.lib is not None:
            self.lib.nvmlShutdown()
            self.lib = None

class HWiNFOLog:
    """Read the public CSV log export, without using the restricted SM2 layout."""
    def __init__(self, path):
        self.path = Path(path)
        self.selected = None

    def read(self):
        result = {'source': 'HWiNFO CSV', 'status': 'offline', 'valueC': None,
                  'sensor': None, 'availableSensors': [],
                  'message': 'HWiNFO 未写入传感器日志'}
        if not self.path.is_file(): return result
        stat = self.path.stat()
        with self.path.open('rb') as handle:
            head = handle.readline(262144)
            offset = max(0, stat.st_size - 131072)
            handle.seek(offset)
            tail = handle.read(131072)
            if offset:
                # The bounded tail may begin inside a quoted field.
                tail = tail.partition(b'\n')[2]
        encoding = 'utf-8-sig'
        try:
            header_text = head.decode(encoding)
            body = tail.decode(encoding)
        except UnicodeDecodeError:
            encoding = 'mbcs'
            header_text = head.decode(encoding, errors='replace')
            body = tail.decode(encoding, errors='replace')
        delimiter = ';' if header_text.count(';') > header_text.count(',') else ','
        header = next(csv.reader([header_text], delimiter=delimiter), [])
        candidates = [(i, name) for i, name in enumerate(header)
                      if re.search(r'CPU\s*Package|CPU封装|CPU\s*封装', name, re.I)
                      and not re.search(r'Throttl|Distance|Limit|Power|Clock|温度限制|功率', name, re.I)
                      and re.search(r'\[.*(?:°C|℃|C).*\]|temperature|温度', name, re.I)]
        # Default HWiNFO CSV headers can contain two identical CPU Package names.
        # Give each actual column a distinct selection key; do not infer DTS vs
        # Enhanced groups from values or silently make both options select one.
        choices = [(index, name, f'{name} · CSV列{index + 1}') for index, name in candidates]
        result['availableSensors'] = [label for _, _, label in choices]
        if not candidates:
            result['message'] = '日志中没有 CPU Package 摄氏温度列'
            return result
        chosen = next((item for item in choices if item[2] == self.selected), None)
        if self.selected is not None and chosen is None:
            result.update(message='日志表头已改变，请重新选择 CPU Package 温度列')
            return result
        if chosen is None: chosen = next((item for item in choices if 'DTS' in item[1]), choices[0])
        column, name, label = chosen
        result.update(sensor=label, sensorHeader=name, sensorColumn=column + 1)
        if time.time() - stat.st_mtime > 6.0:
            result.update(status='stale', message='HWiNFO 日志已停止更新')
            return result
        result['message'] = '等待下一条有效 CPU Package 温度记录'
        # Never replay an earlier row when the newest reading is unavailable.
        # A writer may be between writes, including halfway through a number.
        if not body.endswith(('\n', '\r')):
            return result
        try:
            rows = [row for row in csv.reader(io.StringIO(body), delimiter=delimiter, strict=True)
                    if any(cell.strip() for cell in row)]
        except csv.Error:
            return result
        if not rows:
            return result
        row = rows[-1]
        if len(row) != len(header) or not re.search(r'\d', row[0]):
            return result
        try:
            value = float(row[column].strip().replace(',', '.'))
        except ValueError:
            return result
        if math.isfinite(value) and -30 <= value <= 150:
            result.update(status='online', valueC=value, message='CPU Package 温度已接入')
        return result

class AlienwareExport:
    """Read the local export of the separately elevated firmware worker."""
    def __init__(self, path):
        self.path = Path(path)

    def read(self):
        result = {'source': 'Alienware firmware / AWCC WMI', 'status': 'setup_required',
                  'sensors': [], 'fans': [],
                  'message': '未找到 Alienware 温度导出文件。请在EXE中选择已有导出；新用户需先运行支持设备的温度导出器。'}
        if not self.path.is_file(): return result
        try:
            data = json.loads(self.path.read_text(encoding='utf-8-sig'))
            stamp = finite(data.get('timestampMs'))
            if data.get('schemaVersion') != 1 or stamp is None: raise ValueError('invalid export')
            result.update(timestampMs=stamp, sessionId=data.get('sessionId'))
            age = time.time() * 1000 - stamp
            if age > 6000 or age < -2000:
                result.update(status='stale', message='Alienware 采集已停止更新；等待采集任务恢复')
                return result
            allowed = {'online', 'partial', 'offline', 'permission_required'}
            result['status'] = data.get('status') if data.get('status') in allowed else 'offline'
            result['message'] = str(data.get('message', '等待 Alienware 读数'))[:400]
            for item in data.get('sensors', [])[:32]:
                key = item.get('key')
                if not isinstance(key, str) or not re.fullmatch(r'0x[0-9A-F]{2}', key): continue
                value = finite(item.get('valueC'))
                if value is not None and not 1 <= value <= 150: value = None
                result['sensors'].append({'key': key, 'label': 'AWCC 温度 ' + key, 'valueC': value})
            for item in data.get('fans', [])[:16]:
                key = item.get('key')
                if not isinstance(key, str) or not re.fullmatch(r'0x[0-9A-F]{2}', key): continue
                value = finite(item.get('rpm'))
                if value is not None and not 0 <= value <= 60000: value = None
                related = [v for v in item.get('relatedTemperatureKeys', [])[:32]
                           if isinstance(v, str) and re.fullmatch(r'0x[0-9A-F]{2}', v)]
                result['fans'].append({'key': key, 'rpm': value, 'relatedTemperatureKeys': related})
            return result
        except (OSError, ValueError, TypeError, AttributeError):
            result.update(status='offline', message='Alienware 本机采集文件暂时不可读')
            return result

class Collector:
    def __init__(self, sensor_log, settings_path=None):
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.session = str(uuid.uuid4())
        self.started = iso_now()
        self.sequence = 0
        self.history = deque(maxlen=math.ceil(90 / INTERVAL))
        self.snapshot = None
        self.cache = SystemCache()
        self.gpu = NvidiaProvider()
        self.gpu_data = {'status': 'warming', 'source': 'NVIDIA NVML'}
        self.gpu_tick = None
        self.sensor = HWiNFOLog(sensor_log)
        self.alienware = AlienwareExport(Path(sensor_log).parent / 'alienware-live.json')
        self.audio = AudioProvider()
        self.temperature_source = 'hwinfo_csv'
        self.awcc_temperature_sensor = None
        self.selected_adapter = None
        self.manual_adapter = False
        self.network_prev = None
        self.network_base = None
        self.network_session = str(uuid.uuid4())
        self.network_started = None
        self.threads = []
        self.settings_path = Path(settings_path) if settings_path else None
        if self.settings_path and self.settings_path.is_file():
            try:
                saved = json.loads(self.settings_path.read_text(encoding='utf-8'))
                if isinstance(saved.get('adapter'), str):
                    self.selected_adapter = saved['adapter']
                    self.manual_adapter = True
                if isinstance(saved.get('temperatureSensor'), str): self.sensor.selected = saved['temperatureSensor']
                if isinstance(saved.get('temperatureSource'), str) and saved['temperatureSource'] in {'hwinfo_csv', 'alienware_wmi'}:
                    self.temperature_source = saved['temperatureSource']
                if isinstance(saved.get('awccTemperatureSensor'), str):
                    self.awcc_temperature_sensor = saved['awccTemperatureSensor']
                if isinstance(saved.get('alienwareExportPath'), str):
                    self.alienware.path = Path(saved['alienwareExportPath'])
            except (OSError, ValueError, AttributeError): pass

    def start(self):
        self.audio.start()
        for fn in [self.gpu_loop, self.sample_loop]:
            thread = threading.Thread(target=fn, daemon=True)
            self.threads.append(thread)
            thread.start()

    def close(self):
        self.stop.set()
        self.audio.close()
        for thread in self.threads: thread.join(timeout=2)

    def gpu_loop(self):
        while not self.stop.is_set():
            begin = time.monotonic()
            try: data = self.gpu.read()
            except Exception as exc:
                self.gpu.close()
                data = {'status': 'offline', 'source': 'NVIDIA NVML', 'message': str(exc)}
            with self.lock:
                self.gpu_data = data
                self.gpu_tick = time.monotonic()
            self.stop.wait(max(0.1, INTERVAL - (time.monotonic() - begin)))
        self.gpu.close()

    def network(self, tick):
        counters = psutil.net_io_counters(pernic=True, nowrap=False)
        stats = psutil.net_if_stats()
        addresses = psutil.net_if_addrs()
        options = []
        for name in counters:
            is_up = bool(stats.get(name) and stats[name].isup)
            connected = any(a.address and not a.address.startswith(('127.', '169.254.', '::1', 'fe80:'))
                            for a in addresses.get(name, []) if a.family in (2, 23))
            excluded = bool(re.search(r'loopback|veth|hyper-v|virtual|vmware|bluetooth|docker|wsl|isatap|teredo', name, re.I))
            options.append({'name': name, 'isUp': is_up, 'hasAddress': connected, 'suggested': is_up and connected and not excluded})
        if self.selected_adapter is None and not self.manual_adapter:
            eligible = [o['name'] for o in options if o['suggested']]
            if not eligible: eligible = [o['name'] for o in options if o['isUp'] and not re.search('loopback', o['name'], re.I)]
            if eligible:
                self.selected_adapter = max(eligible, key=lambda n: counters[n].bytes_recv + counters[n].bytes_sent)
        name = self.selected_adapter
        result = {'source': 'Windows / psutil', 'status': 'offline', 'adapter': name,
                  'availableAdapters': options, 'sessionId': self.network_session,
                  'sessionStartedAt': self.network_started, 'downloadBytesPerSecond': None,
                  'uploadBytesPerSecond': None, 'sessionReceivedBytes': None, 'sessionSentBytes': None}
        if name not in counters or not stats.get(name) or not stats[name].isup:
            self.network_prev = None
            return result
        counter = counters[name]
        current = (counter.bytes_recv, counter.bytes_sent)
        rolled = self.network_base is not None and (current[0] < self.network_base[0] or current[1] < self.network_base[1])
        if self.network_prev and (current[0] < self.network_prev[1][0] or current[1] < self.network_prev[1][1]): rolled = True
        if self.network_base is None or rolled:
            self.network_base = current
            self.network_prev = None
            self.network_session = str(uuid.uuid4())
            self.network_started = iso_now()
        result.update(sessionId=self.network_session, sessionStartedAt=self.network_started,
                      sessionReceivedBytes=current[0] - self.network_base[0],
                      sessionSentBytes=current[1] - self.network_base[1], status='warming')
        if self.network_prev:
            dt = tick - self.network_prev[0]
            if 0 < dt <= 5.0:
                result.update(downloadBytesPerSecond=(current[0] - self.network_prev[1][0]) / dt,
                              uploadBytesPerSecond=(current[1] - self.network_prev[1][1]) / dt, status='online')
        self.network_prev = (tick, current)
        return result

    def sample_loop(self):
        psutil.cpu_percent(interval=None)
        psutil.cpu_percent(interval=None, percpu=True)
        previous = time.monotonic()
        while not self.stop.wait(max(0.01, INTERVAL - (time.monotonic() - previous))):
            tick = time.monotonic()
            stamp_ms = round(time.time() * 1000)
            dt = tick - previous
            previous = tick
            cpu = {'source': 'Windows / psutil', 'status': 'offline', 'totalPercent': None,
                   'logicalPercent': None, 'logicalCount': psutil.cpu_count(), 'packageTemperatureC': None,
                   'temperatureC': None}
            memory = {'source': 'Windows / psutil + GetPerformanceInfo', 'status': 'offline'}
            try:
                total = psutil.cpu_percent(interval=None)
                logical = psutil.cpu_percent(interval=None, percpu=True)
                cpu.update(totalPercent=finite(total) if dt <= 5 else None,
                           logicalPercent=[finite(v) for v in logical] if dt <= 5 else None,
                           logicalCount=len(logical), status='online' if dt <= 5 else 'warming')
            except Exception: cpu['message'] = 'Windows CPU 统计暂时不可用'
            try:
                mem = psutil.virtual_memory()
                used = mem.total - mem.available
                cache = self.cache.read()
                memory.update(totalBytes=mem.total, availableBytes=mem.available,
                              usedBytes=used, usedPercent=used / mem.total * 100,
                              systemCacheBytes=cache, status='online' if cache is not None else 'partial')
            except Exception: memory['message'] = 'Windows 内存统计暂时不可用'
            try: sensor = self.sensor.read()
            except Exception: sensor = {'status': 'offline', 'source': 'HWiNFO CSV', 'valueC': None, 'message': 'HWiNFO 日志暂时无法读取', 'availableSensors': []}
            alienware = self.alienware.read()
            with self.lock:
                cpu['temperatureSources'] = {'hwinfo_csv': sensor, 'alienware_wmi': alienware}
                cpu['temperatureSource'] = self.temperature_source
                if self.temperature_source == 'alienware_wmi':
                    chosen = next((item for item in alienware['sensors'] if item['key'] == self.awcc_temperature_sensor), None)
                    provider = {'source': alienware['source'], 'status': alienware['status'],
                                'valueC': chosen.get('valueC') if chosen else None,
                                'sensor': chosen.get('label') if chosen else self.awcc_temperature_sensor,
                                'sensorKey': self.awcc_temperature_sensor,
                                'message': alienware['message']}
                    if alienware['status'] in {'online', 'partial'}:
                        provider['status'] = 'online' if provider['valueC'] is not None else 'offline'
                        provider['message'] = 'CPU 温度使用你选定的 Alienware 传感器' if chosen else '请按 AWCC 显示确认 CPU 对应的传感器并选择'
                else:
                    provider = sensor
                    cpu['packageTemperatureC'] = sensor.get('valueC')
                cpu['temperatureC'] = provider.get('valueC')
                cpu['temperatureProvider'] = provider
                try: network = self.network(tick)
                except Exception: network = {'status': 'offline', 'source': 'Windows / psutil', 'availableAdapters': []}
                gpu = dict(self.gpu_data)
                if self.gpu_tick is None or tick - self.gpu_tick > FRESH_SECONDS:
                    gpu = {'status': 'stale', 'source': 'NVIDIA NVML', 'name': gpu.get('name')}
                self.sequence += 1
                row = {'timestampMs': stamp_ms, 'cpuTotalPercent': cpu.get('totalPercent'),
                       'cpuPackageTemperature': cpu.get('packageTemperatureC'),
                       'cpuTemperature': cpu.get('temperatureC'),
                       'gpuLoadPercent': gpu.get('loadPercent'), 'memoryUsedBytes': memory.get('usedBytes'),
                       'networkDownloadBytesPerSecond': network.get('downloadBytesPerSecond'),
                       'networkUploadBytesPerSecond': network.get('uploadBytesPerSecond')}
                self.history.append(row)
                while self.history and self.history[0]['timestampMs'] < stamp_ms - 60000: self.history.popleft()
                self.snapshot = {'schemaVersion': 1, 'sessionId': self.session,
                    'sessionStartedAt': self.started, 'sequence': self.sequence,
                    'timestamp': iso_now(), 'timestampMs': stamp_ms,
                    'sampleIntervalMs': round(INTERVAL * 1000), 'staleAfterMs': 4000,
                    'cpu': cpu, 'memory': memory, 'gpu': gpu, 'network': network, 'alienware': alienware,
                    'audio': self.audio.read(),
                    'history': list(self.history)}

    def read(self):
        with self.lock:
            if self.snapshot is None:
                return {'schemaVersion': 1, 'sessionId': self.session, 'status': 'warming', 'history': []}
            # JSON is the transport representation; return a detached snapshot.
            return json.loads(json.dumps(self.snapshot, allow_nan=False))

    def read_audio(self):
        return self.audio.read()

    def configure(self, options):
        with self.lock:
            # Validate the complete request against one snapshot before changing
            # live state, histories or the saved configuration.
            adapter = options.get('adapter')
            next_adapter = self.selected_adapter
            name = self.sensor.selected
            source = self.temperature_source
            sensor_key = self.awcc_temperature_sensor
            if adapter is not None:
                available = psutil.net_io_counters(pernic=True)
                if not isinstance(adapter, str) or adapter not in available: raise ValueError('请选择当前存在的网卡')
                next_adapter = adapter
            if 'temperatureSensor' in options:
                name = options['temperatureSensor']
                choices = self.snapshot.get('cpu', {}).get('temperatureSources', {}).get('hwinfo_csv', {}).get('availableSensors', []) if self.snapshot else []
                if not isinstance(name, str) or name not in choices: raise ValueError('请选择日志中的 CPU Package 温度列')
            if 'temperatureSource' in options:
                source = options['temperatureSource']
                if not isinstance(source, str) or source not in {'hwinfo_csv', 'alienware_wmi'}: raise ValueError('温度来源无效')
            if 'awccTemperatureSensor' in options:
                sensor_key = options['awccTemperatureSensor']
                choices = [item['key'] for item in self.snapshot.get('alienware', {}).get('sensors', [])] if self.snapshot else []
                if not isinstance(sensor_key, str) or sensor_key not in choices: raise ValueError('请选择当前 Alienware 温度传感器')
            # Persist the proposed selection first. A failed replace leaves both
            # the previous saved file and the active selection intact.
            if self.settings_path:
                saved = {'adapter': next_adapter, 'temperatureSensor': name,
                         'temperatureSource': source, 'awccTemperatureSensor': sensor_key,
                         'savedAt': iso_now(), 'sensorLogPath': str(self.sensor.path.resolve()),
                         'alienwareExportPath': str(self.alienware.path.resolve())}
                temporary = self.settings_path.with_suffix('.tmp')
                temporary.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding='utf-8')
                temporary.replace(self.settings_path)
            if next_adapter != self.selected_adapter:
                self.selected_adapter = next_adapter
                self.manual_adapter = True
                self.network_prev = self.network_base = None
                self.network_session = str(uuid.uuid4())
                self.network_started = None
                for row in self.history:
                    row['networkDownloadBytesPerSecond'] = row['networkUploadBytesPerSecond'] = None
                if self.snapshot:
                    self.snapshot['network'] = {'status': 'warming', 'adapter': next_adapter, 'source': 'Windows / psutil', 'availableAdapters': self.snapshot.get('network', {}).get('availableAdapters', [])}
                    self.snapshot['history'] = list(self.history)
            self.sensor.selected = name
            self.temperature_source = source
            self.awcc_temperature_sensor = sensor_key
            if any(key in options for key in ('temperatureSensor', 'temperatureSource', 'awccTemperatureSensor')):
                for row in self.history:
                    row['cpuPackageTemperature'] = row['cpuTemperature'] = None
                if self.snapshot:
                    self.snapshot['history'] = list(self.history)
                    self.snapshot['cpu']['temperatureSource'] = self.temperature_source
                    self.snapshot['cpu']['packageTemperatureC'] = None
                    self.snapshot['cpu']['temperatureC'] = None
                    self.snapshot['cpu']['temperatureProvider'].update(status='warming', valueC=None, message='正在读取所选 CPU 温度来源')
            return {'status': 'configured', 'adapter': self.selected_adapter, 'temperatureSensor': self.sensor.selected,
                    'temperatureSource': self.temperature_source, 'awccTemperatureSensor': self.awcc_temperature_sensor}
