"""
sensors/vision_mss.py — The Eyes (mss edition)
══════════════════════════════════════════
Captures the screen using mss and extracts text via winocr.
"""

import logging
import sys
import threading
import time
import difflib

import mss
import winocr
from PIL import Image

if sys.platform == "win32":
    import pygetwindow as gw

logger = logging.getLogger("B.vision.capture")

class VisionSensor:
    def __init__(self, bus):
        self._bus = bus
        self._running = True
        self._last_context_state = ""
        self._force_refresh = threading.Event()
        
        self._thread = threading.Thread(
            target=self._vision_loop,
            daemon=True,
            name="VisionWorker"
        )
        self._thread.start()

        self._bus.subscribe("request_vision_refresh", self._on_request_refresh)
        logger.info("VisionSensor (mss) initialized")

    def _on_request_refresh(self, payload: dict) -> None:
        self._force_refresh.set()

    def _vision_loop(self):
        while self._running:
            try:
                # 1. Get Active Window Title for context
                title = ""
                active_window = None
                if sys.platform == "win32":
                    active_window = gw.getActiveWindow()
                    if active_window:
                        title = active_window.title

                # 2. Grab screen area & extract text
                raw_text = ""
                with mss.mss() as sct:
                    monitor = sct.monitors[1] # Primary monitor
                    
                    if active_window and not active_window.isMinimized:
                        # Use active window bounds, clamped to monitor
                        left = max(monitor["left"], active_window.left)
                        top = max(monitor["top"], active_window.top)
                        right = min(monitor["left"] + monitor["width"], active_window.right)
                        bottom = min(monitor["top"] + monitor["height"], active_window.bottom)
                        width = max(100, right - left)
                        height = max(100, bottom - top)
                    else:
                        # Fallback to center 800x600
                        width, height = 800, 600
                        left = monitor["left"] + (monitor["width"] - width) // 2
                        top = monitor["top"] + (monitor["height"] - height) // 2
                    
                    bbox = {"top": top, "left": left, "width": width, "height": height}
                    
                    sct_img = sct.grab(bbox)
                    img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
                    
                    # OCR (using Windows native OCR via winocr)
                    result = winocr.recognize_pil(img).get()
                    raw_text = result.text.strip()

                # 3. Check for significant changes
                current_context_state = f"{title}\n{raw_text}"
                similarity = difflib.SequenceMatcher(
                    None, self._last_context_state, current_context_state
                ).ratio()
                
                # Activity level check
                activity_level = "high" if similarity < 0.98 else "low"
                self._bus.publish("user_activity_updated", {
                    "level": activity_level,
                    "similarity": similarity
                })

                # Publish context update if similarity changed significantly or forced
                if similarity < 0.95 or self._force_refresh.is_set():
                    self._last_context_state = current_context_state
                    self._force_refresh.clear()
                    
                    self._bus.publish("context_updated", {
                        "window_title": title,
                        "screen_text": raw_text
                    })

            except Exception as e:
                logger.error(f"Vision sensor error: {e}")
            
            # Wait for next check or force refresh
            self._force_refresh.wait(timeout=5.0)

    def stop(self):
        self._running = False
