from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import sys
import re
from companion_core import APP_VERSION, atomic_json, validate_wallpaper

def installation_id(data_root):
    return hashlib.sha256(str(Path(data_root).resolve()).casefold().encode('utf-8')).hexdigest()[:16]

def task_name(data_root):
    return 'MornyeCompanion-Login-' + installation_id(data_root)

def powershell():
    return Path(os.environ.get('SystemRoot','C:/Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'

def current_user_sid():
    executable=Path(os.environ.get('SystemRoot','C:/Windows'))/'System32/whoami.exe'
    result=subprocess.run([str(executable),'/user','/fo','csv','/nh'],capture_output=True,
        encoding='utf-8',errors='replace',creationflags=subprocess.CREATE_NO_WINDOW,timeout=10,check=True)
    match=re.search(r'S-1-5-(?:\d+-)*\d+',result.stdout)
    if not match: raise RuntimeError('无法识别当前Windows用户。')
    return match.group()

def task_request(data_root, wallpaper, executable, action):
    wallpaper=validate_wallpaper(wallpaper)
    executable=Path(executable).resolve()
    return {'action':action,'taskName':task_name(data_root), 'executable':str(executable),
        'arguments':subprocess.list2cmdline(['--background','--bind',str(wallpaper),'--data-directory',str(Path(data_root).resolve())]),
        'description':'Mornye companion login: '+str(Path(data_root).resolve())}

def run_task_request(data_root, request, runner=subprocess.run):
    request_path=Path(data_root)/'startup-request.json'
    atomic_json(request_path, request)
    result=runner([str(powershell()),'-NoProfile','-NonInteractive','-WindowStyle','Hidden',
        '-ExecutionPolicy','Bypass','-File',str(Path(__file__).with_name('startup-task.ps1')),
        '-RequestPath',str(request_path)],capture_output=True,encoding='utf-8',errors='replace',
        creationflags=subprocess.CREATE_NO_WINDOW,timeout=30)
    if result.returncode:
        raise RuntimeError('登录启动设置未完成：'+(result.stderr.strip() or result.stdout.strip())[-1600:])
    return json.loads(result.stdout.lstrip('\ufeff'))

def install_startup(data_root, wallpaper, executable=None, runner=subprocess.run):
    if executable is None and not getattr(sys,'frozen',False):
        raise RuntimeError('请使用打包后的 EXE 设置登录启动。')
    executable=Path(executable or sys.executable).resolve()
    if not executable.is_file(): raise ValueError('找不到采集器 EXE。')
    # Versioned path: enabling an update never overwrites a running older binary.
    destination=Path(data_root)/'program'/APP_VERSION/'Mornye-Telemetry.exe'
    destination.parent.mkdir(parents=True,exist_ok=True)
    if executable!=destination.resolve() and (not destination.exists() or hashlib.sha256(executable.read_bytes()).digest()!=hashlib.sha256(destination.read_bytes()).digest()):
        shutil.copyfile(executable,destination.with_suffix('.new'))
        destination.with_suffix('.new').replace(destination)
    request=task_request(data_root,wallpaper,destination,'install')
    state=run_task_request(data_root,request,runner)
    atomic_json(Path(data_root)/'startup.json',dict(state, executable=str(destination.resolve()),
        wallpaperDirectory=str(Path(wallpaper).resolve()),version=APP_VERSION))
    return state

def startup_state(data_root):
    try:
        state = json.loads((Path(data_root)/'startup.json').read_text(encoding='utf-8'))
        if isinstance(state, dict) and isinstance(state.get('installed'), bool):
            return state
        return {'installed':False}
    except (OSError,ValueError): return {'installed':False}

def remove_startup(data_root, wallpaper, runner=subprocess.run):
    state=startup_state(data_root)
    if not state.get('executable'): return {'installed':False}
    result=run_task_request(data_root,task_request(data_root,wallpaper,state['executable'],'remove'),runner)
    atomic_json(Path(data_root)/'startup.json',dict(state,installed=False))
    return result

def resume_temperature(app):
    """Never asks for UAC during login. Persistent installation has its own task."""
    from persistent_sensors import PersistentExporter, persistent_state
    if app.collector.temperature_source!='alienware_wmi': return
    if persistent_state(app.data_root).get('installed'):
        if isinstance(app.managed_exporter,PersistentExporter) and app.managed_exporter.poll() is None:
            return
        if app.managed_exporter:
            app.managed_exporter.stop()
        manager=PersistentExporter(app.data_root)
        manager.start()
        app.managed_exporter=manager
        with app.collector.lock:
            app.collector.alienware.path=manager.export_path
            app.collector.configure({'temperatureSource':'alienware_wmi'})
        return
    # Existing external output remains configured and is read by the collector;
    # it may become fresh a few seconds after the companion starts.
