"""
sensors/vision_semantic.py — B's Semantic Context Engine
════════════════════════════════════════════════════════

Abandons OCR entirely. Uses Windows UIAutomation to read the underlying
accessibility tree of the active window. Extracts perfect text instantly
with near-zero CPU overhead.
"""

import threading
import time
import hashlib
import logging
from typing import Optional

import uiautomation as auto

logger = logging.getLogger("B.vision.semantic")

class SemanticVisionSensor:
    def __init__(self, bus, check_interval: int = 3):
        self._bus = bus
        self.check_interval = check_interval
        self._last_hash = ""
        self._is_running = False
        self._force_refresh = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # Speed up UIAutomation global settings
        auto.uiautomation.SetGlobalSearchTimeout(1.0) 

        self._bus.subscribe("request_vision_refresh", self._on_request_refresh)

    def _on_request_refresh(self, payload: dict) -> None:
        self._force_refresh.set()

    def start(self):
        if not self._is_running:
            self._is_running = True
            self._thread = threading.Thread(target=self._glance_loop, daemon=True, name="SemanticVision")
            self._thread.start()
            logger.info("Semantic Vision activated. Hooking OS Accessibility Tree every %ss", self.check_interval)

    def stop(self):
        self._is_running = False

    def _extract_smart_context(self, window) -> str:
        """
        Intelligently hunts for the main content block of the active window.
        Bypasses navigation bars, sidebars, and menus.
        """
        text_content = ""
        
        try:
            # 1. Look for a Document Control (Browsers, Word, PDFs)
            main_control = window.DocumentControl()
            
            # 2. If not a document, look for an Edit Control (VS Code, Notepad)
            if not main_control.Exists(0, 0):
                main_control = window.EditControl()
            
            # 3. Extract the raw text from the control
            if main_control.Exists(0, 0):
                # Try to get the raw value pattern first (most accurate for text fields)
                try:
                    text_content = main_control.GetValuePattern().Value
                except Exception:
                    # Fallback to the accessible name property
                    text_content = main_control.Name
            else:
                # 4. Fallback: If it's an unmapped app, just grab the window's accessible name
                text_content = window.Name

        except Exception as e:
            logger.debug("UIA extraction failed on current window: %s", e)

        return str(text_content).strip()

    def _glance_loop(self):
        """The background loop that acts as B's semantic mind-reader."""
        while self._is_running:
            try:
                # 1. Instantly grab the foreground window OS object
                active_window = auto.GetForegroundControl()
                
                if not active_window:
                    time.sleep(self.check_interval)
                    continue
                    
                window_title = active_window.Name

                # Ignore the desktop, taskbar, or B's own UI
                if not window_title or "B" == window_title or "Program Manager" in window_title:
                    time.sleep(self.check_interval)
                    continue

                # 2. Extract the semantic text perfectly
                raw_text = self._extract_smart_context(active_window)

                if len(raw_text) < 10:
                    time.sleep(self.check_interval)
                    continue

                # Take the first 1000 characters (expanded since it's cleaner)
                snippet = raw_text[:1000]

                # 3. Hash Check
                current_state = f"{window_title}||{snippet}"
                current_hash = hashlib.md5(current_state.encode('utf-8')).hexdigest()

                if current_hash != self._last_hash or self._force_refresh.is_set():
                    self._last_hash = current_hash
                    self._force_refresh.clear()
                    
                    # 4. Fire the event!
                    payload = {
                        "window_title": window_title,
                        "screen_text": snippet
                    }
                    # We use 'context_updated' to remain compatible with existing subscribers
                    self._bus.publish("context_updated", payload)
                    logger.debug("Semantic context updated: %s", window_title)

            except Exception as e:
                logger.error("Semantic Sensor error: %s", e)

            # Rest the thread
            self._force_refresh.wait(timeout=self.check_interval)
