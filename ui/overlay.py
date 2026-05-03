"""
ui/overlay.py — The Ghost Window
═════════════════════════════════

Frameless, transparent, always-on-top, click-through overlay.
Uses a QRegion mask for rounded corners — clips at the OS pixel
level, so no antialiasing artifacts or DWM border leaks.
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QPainterPath, QRegion, QTransform
from PyQt6.QtWidgets import QWidget

if TYPE_CHECKING:
    from core.bus import EventBus

_HAS_WIN32 = False
if sys.platform == "win32":
    try:
        import win32con
        import win32gui
        _HAS_WIN32 = True
    except ImportError:
        pass

logger = logging.getLogger("B.ui.overlay")

CANVAS_W = 180
CANVAS_H = 112
CORNER_RADIUS = 12  # Subtle rounding — visible but not dramatic


class WindowManager(QWidget):

    def __init__(self, bus: EventBus) -> None:
        super().__init__()
        self._bus = bus

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setStyleSheet("background: transparent;")
        self.setFixedSize(CANVAS_W, CANVAS_H)

        self._bus.subscribe("position_updated", self._on_position_updated, priority=0)
        logger.info("WindowManager initialized (%dx%d canvas)", CANVAS_W, CANVAS_H)

    def initialize(self) -> None:
        self.show()

        # ──────────────────────────────────────────────────────────────
        # OS-level pixel mask — the nuclear option for border artifacts.
        #
        # QRegion.setMask() tells Windows: "these are the ONLY pixels
        # that exist for this window." Everything outside the mask is
        # not just transparent — it literally doesn't exist at the OS
        # level. No DWM border, no antialiasing fringe, no artifacts.
        #
        # The tradeoff: corners are pixel-stepped (no smooth AA). At
        # radius=12 on a 180×112 window, this is barely noticeable.
        # ──────────────────────────────────────────────────────────────
        path = QPainterPath()
        path.addRoundedRect(
            QRectF(0, 0, CANVAS_W, CANVAS_H),
            CORNER_RADIUS, CORNER_RADIUS,
        )
        mask = QRegion(path.toFillPolygon(QTransform()).toPolygon())
        self.setMask(mask)

        self._apply_click_through()

    def _apply_click_through(self) -> None:
        if not _HAS_WIN32:
            logger.warning("Win32 API not available — click-through disabled.")
            return

        hwnd = int(self.winId())

        # Click-through
        style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        style |= win32con.WS_EX_LAYERED | win32con.WS_EX_TRANSPARENT
        win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, style)

        # Kill DWM border artifacts (safe — doesn't conflict with Qt alpha)
        import ctypes
        try:
            dwm = ctypes.windll.dwmapi
            # Disable non-client rendering (kills DWM border)
            val = ctypes.c_int(1)
            dwm.DwmSetWindowAttribute(hwnd, 2, ctypes.byref(val), 4)
            # Disable Win11 rounded corners
            val2 = ctypes.c_int(3)
            dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(val2), 4)
        except Exception:
            pass

        logger.info("Click-through applied (HWND=0x%08X)", hwnd)
        self._enforce_topmost()

    def _enforce_topmost(self) -> None:
        if not _HAS_WIN32:
            return
        hwnd = int(self.winId())
        win32gui.SetWindowPos(
            hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
            win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE
        )

    def _on_position_updated(self, payload: dict) -> None:
        self.move(int(payload["x"]), int(payload["y"]))
        self._enforce_topmost()
