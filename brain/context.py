import logging
import time
import threading
import sys

if sys.platform == "win32":
    import win32gui
    import win32process
    import psutil

logger = logging.getLogger("B.brain.context")

class ContextEngine:
    """
    Observes the user's environment (active window, etc.) 
    to give B context for "sentient" commentary.
    """
    def __init__(self, bus):
        self._bus = bus
        self._active_app = ""
        self._active_title = ""
        self._running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()

    def _monitor_loop(self):
        while self._running:
            try:
                if sys.platform == "win32":
                    hwnd = win32gui.GetForegroundWindow()
                    title = win32gui.GetWindowText(hwnd)
                    
                    _, pid = win32process.GetWindowThreadProcessId(hwnd)
                    try:
                        # Use a shorter timeout or check if process still exists
                        if psutil.pid_exists(pid):
                            proc = psutil.Process(pid)
                            with proc.oneshot():
                                app_name = proc.name()
                        else:
                            app_name = "Unknown"
                    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.TimeoutExpired):
                        app_name = "Unknown"
                    except Exception:
                        app_name = "Unknown"

                    if title != self._active_title or app_name != self._active_app:
                        self._active_title = title
                        self._active_app = app_name
                        self._bus.publish("context_updated", {
                            "app": app_name,
                            "title": title
                        })
            except Exception:
                pass
            time.sleep(2.0) # Check every 2 seconds

    def stop(self):
        self._running = False
