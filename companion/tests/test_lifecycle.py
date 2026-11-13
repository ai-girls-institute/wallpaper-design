from pathlib import Path
import ctypes
import json
import queue
import tempfile
import time
import unittest
import urllib.request
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from companion_core import Runtime
from windows_tray import WindowsTray, WindowLifecycle, CALLBACK, NotifyData, WindowClass

class Root:
    def __init__(self): self.calls = []
    def withdraw(self): self.calls.append('hide')
    def iconify(self): self.calls.append('minimize')
    def deiconify(self): self.calls.append('show')
    def lift(self): self.calls.append('lift')
    def destroy(self): self.calls.append('destroy')

class LifecycleChecks(unittest.TestCase):
    def test_background_keeps_api_alive_then_explicit_exit_stops(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = Runtime(temporary, Path(__file__).resolve().parents[1] / 'configure.html', 0)
            tray = WindowsTray()
            root = Root()
            try:
                tray.start()
                app.start()
                life = WindowLifecycle(root, tray, app.close)
                life.background()
                self.assertEqual(root.calls, ['hide'])
                request = urllib.request.Request(f'http://127.0.0.1:{app.port}/api/v1/snapshot',
                    headers={'Origin':'null','X-Wallpaper-Read-Key':app.key})
                self.assertEqual(urllib.request.urlopen(request, timeout=3).status, 200)
                life.dispatch('show')
                self.assertFalse(life.closed)
                self.assertIsNotNone(app.server)
                life.dispatch('exit')
                life.exit()  # idempotent cleanup
                self.assertIsNone(app.server)
                self.assertFalse(tray.thread.is_alive())
                self.assertEqual(root.calls.count('destroy'), 1)
                self.assertEqual(json.loads((Path(temporary)/'runtime-state.json').read_text())['status'], 'stopped')
            finally:
                app.close()
                tray.close()

    def test_tray_unavailable_keeps_taskbar_escape_and_recovers_window(self):
        root = Root()
        cleanup = []
        life = WindowLifecycle(root, None, lambda:cleanup.append(1))
        life.background()
        self.assertEqual(root.calls, ['minimize'])
        life.dispatch('unavailable')
        self.assertEqual(root.calls[-2:], ['show','lift'])
        self.assertEqual(cleanup, [])
        life.exit()
        self.assertEqual(cleanup, [1])

    def test_native_callback_commands_and_readd(self):
        result = native_check()
        self.assertEqual(result['events'], ['show', 'show', 'exit'])

def native_check():
    tray = WindowsTray()
    try:
        tray.start()
        assert ctypes.sizeof(NotifyData) == 976
        assert ctypes.sizeof(WindowClass) == 72
        assert tray.user.PostMessageW(tray.hwnd, CALLBACK, 1, 0x203)
        assert tray.events.get(timeout=3) == 'show'
        assert tray.user.PostMessageW(tray.hwnd, 0x111, 1, 0)
        assert tray.events.get(timeout=3) == 'show'
        assert tray.user.PostMessageW(tray.hwnd, 0x111, 2, 0)
        assert tray.events.get(timeout=3) == 'exit'
        # Re-add only this candidate icon; never restart the real Explorer process.
        tray.shell.Shell_NotifyIconW(2, ctypes.byref(tray.icon))
        tray.available = False
        assert tray.user.PostMessageW(tray.hwnd, tray.restart_message, 0, 0)
        deadline = time.monotonic() + 3
        while not tray.available and time.monotonic() < deadline: time.sleep(.02)
        assert tray.available
    finally:
        tray.close()
    assert not tray.thread.is_alive()
    return {'events':['show','show','exit'], 'iconReadded':True, 'threadStopped':True,
            'scope':'Own native icon and injected Win32 messages; no visual acceptance'}

if __name__ == '__main__': unittest.main()
