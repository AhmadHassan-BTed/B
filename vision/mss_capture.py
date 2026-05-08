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
        self._current_metadata = {}
        self._force_refresh = threading.Event()
        self._force_next = False
        self._is_paused = False
        
        self._thread = threading.Thread(
            target=self._vision_loop,
            daemon=True,
            name="VisionWorker"
        )
        self._thread.start()

        self._bus.subscribe("request_vision_refresh", self._on_request_refresh)
        self._bus.subscribe("semantic_extraction_failed", self._on_request_refresh)
        self._bus.subscribe("active_window_changed", self._on_window_changed)
        self._bus.subscribe("b_thinking", self._on_brain_busy)
        self._bus.subscribe("b_finished_thinking", self._on_brain_idle)
        logger.info("VisionSensor (mss) initialized | Listening for semantic fallbacks")

    def _on_window_changed(self, payload: dict) -> None:
        self._current_metadata = payload
        self._force_next = True
        self._force_refresh.set()

    def _on_request_refresh(self, payload: dict) -> None:
        self._force_next = True
        self._force_refresh.set()

    def _on_brain_busy(self, payload: dict) -> None:
        self._is_paused = True

    def _on_brain_idle(self, payload: dict) -> None:
        self._is_paused = False
        self._force_refresh.set()

    def _vision_loop(self):
        while self._running:
            if self._is_paused:
                self._force_refresh.wait(timeout=5.0)
                continue
            try:
                # 1. Get Active Window Title for context
                title = ""
                active_window = None
                if sys.platform == "win32":
                    active_window = gw.getActiveWindow()
                    if active_window:
                        title = active_window.title
                        # Safety: skip B's own window
                        t_low = title.lower()
                        if "antigravity" in t_low or t_low == "b" or t_low.startswith("b -"):
                            time.sleep(1.0)
                            continue

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
                    
                    spatial_map = {}
                    text_parts = []
                    node_id = 1
                    
                    for line in result.lines:
                        if not line.words:
                            continue
                        
                        min_x = min(w.bounding_rect.x for w in line.words)
                        min_y = min(w.bounding_rect.y for w in line.words)
                        max_x = max(w.bounding_rect.x + w.bounding_rect.width for w in line.words)
                        max_y = max(w.bounding_rect.y + w.bounding_rect.height for w in line.words)
                        
                        center_x = left + min_x + (max_x - min_x) / 2
                        center_y = top + min_y + (max_y - min_y) / 2
                        
                        spatial_map[str(node_id)] = (center_x, center_y, line.text[:60])
                        text_parts.append(f"[#{node_id}] {line.text}")
                        node_id += 1
                        
                    raw_text = "\n".join(text_parts)

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
                    
                    # Calculate a simple quality score for OCR (variety based)
                    quality = 0.0
                    if raw_text:
                        unique_chars = len(set(raw_text[:200]))
                        quality = min(0.9, (unique_chars / 200) * 1.5) if len(raw_text) > 50 else 0.1

                    self._bus.publish("context_updated", {
                        "window_title": title,
                        "screen_text": raw_text,
                        "app_type": self._current_metadata.get("app_type", "unknown"),
                        "extraction_source": "ocr_vision",
                        "quality_score": round(quality, 2),
                        "content_length": len(raw_text),
                        "spatial_map": spatial_map,
                    })

            except Exception as e:
                logger.error(f"Vision sensor error: {e}")
            
            # Wait for next check or force refresh
            timeout = 1.0 if self._force_next else 10.0
            self._force_next = False
            self._force_refresh.wait(timeout=timeout)

    def stop(self):
        self._running = False
