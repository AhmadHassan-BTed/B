"""
sensors/window_tracker.py — The Focus Monitor
══════════════════════════════════════════
Tracks the user's active window and publishes updates.
"""

import logging
import sys
import threading
import time

if sys.platform == "win32":
    import pygetwindow as gw

logger = logging.getLogger("B.sensors.window_tracker")

class WindowTracker:
    def __init__(self, bus):
        self._bus = bus
        self._running = True
        self._last_title = ""
        
        self._thread = threading.Thread(
            target=self._tracking_loop,
            daemon=True,
            name="WindowTracker"
        )
        self._thread.start()
        logger.info("WindowTracker initialized")

    def _tracking_loop(self):
        while self._running:
            try:
                title = ""
                if sys.platform == "win32":
                    active_window = gw.getActiveWindow()
                    if active_window:
                        title = active_window.title
                        
                if title != self._last_title:
                    self._last_title = title
                    self._bus.publish("active_window_changed", {
                        "title": title
                    })
                    logger.debug(f"Window changed: {title}")
                    
            except Exception as e:
                logger.error(f"Window tracker error: {e}")
            
            time.sleep(2.0) # Check every 2 seconds

    def stop(self):
        self._running = False
