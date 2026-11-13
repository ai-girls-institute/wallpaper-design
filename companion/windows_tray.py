"""Native notification icon. Its thread never calls Tk; events use a queue."""
import ctypes as c
from ctypes import wintypes as w
import queue
import threading
import uuid
import logging

LRESULT = c.c_ssize_t
WPARAM = c.c_size_t
LPARAM = c.c_ssize_t
WNDPROC = c.WINFUNCTYPE(LRESULT, w.HWND, w.UINT, WPARAM, LPARAM)
CALLBACK = 0x8001
STOP = 0x8002
RETRY_TIMER = 1
RETRY_MS = 2000
logger = logging.getLogger('mornye.tray')

class GUID(c.Structure):
    _fields_ = [('Data1', w.DWORD), ('Data2', w.WORD), ('Data3', w.WORD), ('Data4', w.BYTE * 8)]

class NotifyData(c.Structure):
    _fields_ = [('cbSize', w.DWORD), ('hWnd', w.HWND), ('uID', w.UINT),
                ('uFlags', w.UINT), ('uCallbackMessage', w.UINT), ('hIcon', w.HICON),
                ('szTip', w.WCHAR * 128), ('dwState', w.DWORD), ('dwStateMask', w.DWORD),
                ('szInfo', w.WCHAR * 256), ('uVersion', w.UINT), ('szInfoTitle', w.WCHAR * 64),
                ('dwInfoFlags', w.DWORD), ('guidItem', GUID), ('hBalloonIcon', w.HICON)]

class WindowClass(c.Structure):
    _fields_ = [('style', w.UINT), ('lpfnWndProc', WNDPROC), ('cbClsExtra', c.c_int),
                ('cbWndExtra', c.c_int), ('hInstance', w.HINSTANCE), ('hIcon', w.HICON),
                ('hCursor', w.HANDLE), ('hbrBackground', w.HBRUSH),
                ('lpszMenuName', w.LPCWSTR), ('lpszClassName', w.LPCWSTR)]

class WindowsTray:
    def __init__(self):
        self.events = queue.SimpleQueue()
        self.ready = threading.Event()
        self.stopping = threading.Event()
        self.hwnd = None
        self.error = None
        self.available = False
        self.thread = None
        self.retry_count = 0
        self.last_error = 0

    def start(self):
        self.thread = threading.Thread(target=self._run, name='notification-icon', daemon=True)
        self.thread.start()
        if not self.ready.wait(5):
            self.close()
            raise RuntimeError('系统托盘初始化超时，请使用任务栏最小化。')
        if self.error:
            raise RuntimeError('系统托盘不可用，请使用任务栏最小化。') from self.error

    def _apis(self):
        self.user = c.WinDLL('user32', use_last_error=True)
        self.shell = c.WinDLL('shell32', use_last_error=True)
        self.kernel = c.WinDLL('kernel32', use_last_error=True)
        specs = {
            'RegisterClassW': ([c.POINTER(WindowClass)], w.WORD),
            'UnregisterClassW': ([w.LPCWSTR, w.HINSTANCE], w.BOOL),
            'CreateWindowExW': ([w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, c.c_int, c.c_int,
                                c.c_int, c.c_int, w.HWND, w.HMENU, w.HINSTANCE, c.c_void_p], w.HWND),
            'DefWindowProcW': ([w.HWND, w.UINT, WPARAM, LPARAM], LRESULT),
            'DestroyWindow': ([w.HWND], w.BOOL),
            'PostMessageW': ([w.HWND, w.UINT, WPARAM, LPARAM], w.BOOL),
            'GetMessageW': ([c.POINTER(w.MSG), w.HWND, w.UINT, w.UINT], c.c_int),
            'TranslateMessage': ([c.POINTER(w.MSG)], w.BOOL),
            'DispatchMessageW': ([c.POINTER(w.MSG)], LRESULT),
            'PostQuitMessage': ([c.c_int], None),
            'LoadIconW': ([w.HINSTANCE, c.c_void_p], w.HICON),
            'RegisterWindowMessageW': ([w.LPCWSTR], w.UINT),
            'SetTimer': ([w.HWND, c.c_size_t, w.UINT, c.c_void_p], c.c_size_t),
            'KillTimer': ([w.HWND, c.c_size_t], w.BOOL),
            'CreatePopupMenu': ([], w.HMENU),
            'AppendMenuW': ([w.HMENU, w.UINT, WPARAM, w.LPCWSTR], w.BOOL),
            'DestroyMenu': ([w.HMENU], w.BOOL),
            'GetCursorPos': ([c.POINTER(w.POINT)], w.BOOL),
            'SetForegroundWindow': ([w.HWND], w.BOOL),
            'TrackPopupMenu': ([w.HMENU, w.UINT, c.c_int, c.c_int, c.c_int, w.HWND, c.c_void_p], w.UINT),
        }
        for name, (args, result) in specs.items():
            fn = getattr(self.user, name)
            fn.argtypes, fn.restype = args, result
        self.kernel.GetModuleHandleW.argtypes = [w.LPCWSTR]
        self.kernel.GetModuleHandleW.restype = w.HMODULE
        self.shell.Shell_NotifyIconW.argtypes = [w.DWORD, c.POINTER(NotifyData)]
        self.shell.Shell_NotifyIconW.restype = w.BOOL

    def _add_icon(self):
        was_available = self.available
        c.set_last_error(0)
        self.available = bool(self.shell.Shell_NotifyIconW(0, c.byref(self.icon)))
        self.last_error = c.get_last_error() if not self.available else 0
        if self.available:
            self.user.KillTimer(self.hwnd, RETRY_TIMER)
            if self.retry_count:
                logger.info('Notification icon recovered after %s retries', self.retry_count)
                self.events.put('recovered')
            self.retry_count = 0
        else:
            self.retry_count += 1
            if self.retry_count == 1 or self.retry_count % 30 == 0:
                logger.warning('Notification icon unavailable; retry=%s winerror=%s', self.retry_count, self.last_error)
            if not self.user.SetTimer(self.hwnd, RETRY_TIMER, RETRY_MS, None):
                raise c.WinError(c.get_last_error())
            if was_available or self.retry_count == 1:
                self.events.put('unavailable')
        return self.available

    def _menu(self):
        menu = self.user.CreatePopupMenu()
        if not menu:
            self.events.put('show')
            return
        try:
            self.user.AppendMenuW(menu, 0, 1, '打开采集器设置')
            self.user.AppendMenuW(menu, 0x800, 0, None)
            self.user.AppendMenuW(menu, 0, 2, '退出采集器（停止采集）')
            point = w.POINT()
            self.user.GetCursorPos(c.byref(point))
            self.user.SetForegroundWindow(self.hwnd)
            selected = self.user.TrackPopupMenu(menu, 0x100 | 0x2, point.x, point.y, 0, self.hwnd, None)
            self.user.PostMessageW(self.hwnd, 0, 0, 0)
            if selected in (1, 2):
                self.events.put('show' if selected == 1 else 'exit')
        finally:
            self.user.DestroyMenu(menu)

    def _run(self):
        registered = False
        try:
            self._apis()
            instance = self.kernel.GetModuleHandleW(None)
            name = 'MornyeTray-' + str(uuid.uuid4())
            self.restart_message = self.user.RegisterWindowMessageW('TaskbarCreated')
            @WNDPROC
            def procedure(hwnd, msg, wp, lp):
                # Exceptions must not unwind across a ctypes native callback.
                try:
                    if msg == CALLBACK:
                        if lp in (0x203, 0x400, 0x401):
                            self.events.put('show')
                        elif lp in (0x205, 0x7B):
                            self._menu()
                        return 0
                    if msg == 0x111:  # menu command, also used by isolated technical checks
                        command = wp & 0xFFFF
                        if command in (1, 2):
                            self.events.put('show' if command == 1 else 'exit')
                        return 0
                    if msg == self.restart_message and hasattr(self, 'icon'):
                        self.available = False
                        self._add_icon()
                        return 0
                    if msg == 0x113 and wp == RETRY_TIMER:
                        if not self.stopping.is_set() and not self.available:
                            self._add_icon()
                        return 0
                    if msg in (STOP, 0x10):
                        self.user.DestroyWindow(hwnd)
                        return 0
                    if msg == 2:
                        self.user.PostQuitMessage(0)
                        return 0
                except Exception:
                    self.events.put('unavailable')
                    return 0
                return self.user.DefWindowProcW(hwnd, msg, wp, lp)
            self.procedure = procedure
            definition = WindowClass()
            definition.lpfnWndProc, definition.hInstance, definition.lpszClassName = procedure, instance, name
            if not self.user.RegisterClassW(c.byref(definition)):
                raise c.WinError(c.get_last_error())
            registered = True
            # Hidden top-level window receives TaskbarCreated broadcasts.
            self.hwnd = self.user.CreateWindowExW(0, name, name, 0, 0, 0, 0, 0, None, None, instance, None)
            if not self.hwnd:
                raise c.WinError(c.get_last_error())
            self.icon = NotifyData()
            self.icon.cbSize, self.icon.hWnd, self.icon.uID = c.sizeof(NotifyData), self.hwnd, 1
            self.icon.uFlags, self.icon.uCallbackMessage = 7, CALLBACK
            self.icon.hIcon = self.user.LoadIconW(None, c.c_void_p(32512))  # shared system application icon
            self.icon.szTip = '星海观测室 · 硬件采集器（双击打开设置）'
            if not self.icon.hIcon:
                raise c.WinError(c.get_last_error())
            # Explorer can still be starting at logon. Keep the message window
            # alive so both TaskbarCreated and the timer can recover the icon.
            self._add_icon()
            self.ready.set()
            if self.stopping.is_set():
                return
            message = w.MSG()
            while True:
                result = self.user.GetMessageW(c.byref(message), None, 0, 0)
                if result == 0:
                    break
                if result == -1:
                    raise c.WinError(c.get_last_error())
                self.user.TranslateMessage(c.byref(message))
                self.user.DispatchMessageW(c.byref(message))
        except Exception as error:
            self.error = error
            self.events.put('unavailable')
        finally:
            if self.hwnd:
                self.user.KillTimer(self.hwnd, RETRY_TIMER)
            if hasattr(self, 'icon'):
                self.shell.Shell_NotifyIconW(2, c.byref(self.icon))
            if self.hwnd:
                self.user.DestroyWindow(self.hwnd)
                self.hwnd = None
            if registered:
                self.user.UnregisterClassW(name, instance)
            self.available = False
            self.ready.set()

    def close(self):
        self.stopping.set()
        if self.hwnd:
            self.user.PostMessageW(self.hwnd, STOP, 0, 0)
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=3)

class WindowLifecycle:
    """Only the Tk thread calls these methods. Hiding never stops the runtime."""
    def __init__(self, root, tray, shutdown):
        self.root, self.tray, self.shutdown = root, tray, shutdown
        self.closed = False
        self.pending_background = False

    def background(self):
        if self.closed:
            return
        self.pending_background = True
        if self.tray and self.tray.available:
            self.root.withdraw()
        else:
            self.root.iconify()

    def show(self, clear_background=True):
        if clear_background:
            self.pending_background = False
        self.root.deiconify()
        self.root.lift()

    def on_unmap(self, event):
        if event.widget is self.root and self.root.state() == 'iconic':
            if self.tray and self.tray.available:
                self.background()
            else:
                self.pending_background = True

    def dispatch(self, event):
        if event == 'exit':
            self.exit()
        elif event == 'show':
            self.show()
        elif event == 'unavailable':
            self.show(clear_background=False)
        elif event == 'recovered' and self.pending_background:
            self.background()

    def exit(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.shutdown()
        finally:
            if self.tray:
                self.tray.close()
            self.root.destroy()
