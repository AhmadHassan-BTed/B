"""
sensors/vision.py — The Eyes
════════════════════════════
Runs on a slow background thread, capturing the center of the screen
and extracting raw text via OCR.
"""

from __future__ import annotations

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

logger = logging.getLogger("B.sensors.vision")

class VisionSensor:
    def __init__(self, bus):
        self._bus = bus
        self._running = True
        self._last_context_state = ""
        
        self._thread = threading.Thread(
            target=self._vision_loop,
            daemon=True,
            name="VisionWorker"
        )
        self._thread.start()
        logger.info("VisionSensor initialized")

    def _vision_loop(self):
        while self._running:
            try:
                # 1. Get Active Window Title
                title = ""
                if sys.platform == "win32":
                    active_window = gw.getActiveWindow()
                    if active_window:
                        title = active_window.title

                # 2. Grab screen center & extract text
                raw_text = ""
                with mss.mss() as sct:
                    # In mss, monitors[1] is the primary monitor
                    monitor = sct.monitors[1]
                    
                    width, height = 800, 600
                    left = monitor["left"] + (monitor["width"] - width) // 2
                    top = monitor["top"] + (monitor["height"] - height) // 2
                    
                    bbox = {"top": top, "left": left, "width": width, "height": height}
                    
                    sct_img = sct.grab(bbox)
                    img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
                    
                    # OCR (using Windows native OCR via winocr)
                    result = winocr.recognize_pil(img).get()
                    raw_text = result.text.strip()

                # 3. Check for significant changes and monitor activity
                if title or raw_text:
                    current_context_state = f"{title}\n{raw_text}"
                    
                    # Calculate similarity with previous state
                    similarity = difflib.SequenceMatcher(
                        None, self._last_context_state, current_context_state
                    ).ratio()
                    
                    # ACTIVITY MONITOR: If similarity is low (e.g. < 0.98), user is active
                    activity_level = "low"
                    if similarity < 0.98:
                        activity_level = "high"
                    
                    self._bus.publish("user_activity_updated", {
                        "level": activity_level,
                        "similarity": similarity
                    })

                    # If similarity is less than 85%, consider it a significant change for context
                    if similarity < 0.85:
                        self._last_context_state = current_context_state
                        
                        logger.debug(f"Significant visual context change (Sim: {similarity:.2f})")
                        self._bus.publish("context_updated", {
                            "window_title": title,
                            "screen_text": raw_text
                        })

            except Exception as e:
                logger.error(f"Vision sensor error: {e}")
            
            # Slow background check every 15 seconds
            time.sleep(15.0)

    def stop(self):
        self._running = False
