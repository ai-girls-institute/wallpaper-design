"""Fault injection is confined to this test's own icon and temporary runtime."""
import ctypes as c
import queue
import time
import unittest
from types import SimpleNamespace
from windows_tray import WindowsTray, WindowLifecycle, RETRY_TIMER


class DelayedTray(WindowsTray):
    def _apis(self):
        super()._apis()
        native = self.shell.Shell_NotifyIconW
        self.add_calls = 0
        def notify(operation, data):
            if operation == 0:
                self.add_calls += 1
                if self.add_calls <= 2:
                    c.set_last_error(1460)
                    return False
            return native(operation, data)
        self.shell = SimpleNamespace(Shell_NotifyIconW=notify)


class Root:
    def __init__(self): self.calls = []; self.status = 'normal'
    def state(self): return self.status
    def withdraw(self): self.calls.append('hide'); self.status = 'withdrawn'
    def iconify(self): self.calls.append('minimize'); self.status = 'iconic'
    def deiconify(self): self.calls.append('show'); self.status = 'normal'
    def lift(self): self.calls.append('lift')
    def destroy(self): self.calls.append('destroy')


class RecoveryChecks(unittest.TestCase):
    def test_initial_shell_failure_keeps_thread_and_timer_recovers(self):
        tray = DelayedTray()
        try:
            tray.start()
            self.assertFalse(tray.available)
            self.assertTrue(tray.thread.is_alive())
            self.assertEqual(tray.events.get(timeout=1), 'unavailable')
            # Real Windows timer performs two retries, without a shell restart.
            self.assertEqual(tray.events.get(timeout=7), 'recovered')
            self.assertTrue(tray.available)
            self.assertEqual(tray.add_calls, 3)
            self.assertEqual(tray.retry_count, 0)
            tray.user.PostMessageW(tray.hwnd, 0x113, RETRY_TIMER, 0)
            time.sleep(.1)
            self.assertEqual(tray.add_calls, 3)
        finally:
            tray.close()
        self.assertFalse(tray.thread.is_alive())

    def test_shell_restart_failure_then_recovery(self):
        tray = WindowsTray()
        try:
            tray.start()
            self.assertTrue(tray.available)
            native = tray.shell.Shell_NotifyIconW
            native(2, c.byref(tray.icon))
            fail = [True]
            def notify(operation, data):
                if operation == 0 and fail and fail.pop():
                    return False
                return native(operation, data)
            tray.shell = SimpleNamespace(Shell_NotifyIconW=notify)
            tray.user.PostMessageW(tray.hwnd, tray.restart_message, 0, 0)
            self.assertEqual(tray.events.get(timeout=3), 'unavailable')
            self.assertEqual(tray.events.get(timeout=4), 'recovered')
            self.assertTrue(tray.available)
        finally:
            tray.close()

    def test_recovery_hides_only_pending_background_request(self):
        root = Root()
        tray = SimpleNamespace(available=False)
        life = WindowLifecycle(root, tray, lambda: None)
        life.background()
        self.assertEqual(root.calls, ['minimize'])
        life.dispatch('unavailable')
        tray.available = True
        life.dispatch('recovered')
        self.assertEqual(root.status, 'withdrawn')
        life.dispatch('show')
        life.dispatch('recovered')
        self.assertEqual(root.status, 'normal')

    def test_minimize_filters_children_and_withdraw_events(self):
        root = Root()
        life = WindowLifecycle(root, SimpleNamespace(available=True), lambda: None)
        life.on_unmap(SimpleNamespace(widget=object()))
        self.assertEqual(root.calls, [])
        root.status = 'iconic'
        life.on_unmap(SimpleNamespace(widget=root))
        self.assertEqual(root.calls, ['hide'])
        life.on_unmap(SimpleNamespace(widget=root))
        self.assertEqual(root.calls, ['hide'])

    def test_exit_cancels_failed_start_retry(self):
        tray = DelayedTray()
        tray.start()
        tray.close()
        self.assertFalse(tray.thread.is_alive())
        self.assertFalse(tray.available)


if __name__ == '__main__': unittest.main()
