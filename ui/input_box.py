"""
ui/input_box.py — User Input Hook
═════════════════════════════════

A global hotkey-triggered text input field for talking to B.
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QRect, pyqtSignal, QEvent, QTimer
from PyQt6.QtGui import QPainter, QPaintEvent, QColor, QFont, QPainterPath, QRegion, QTransform
from PyQt6.QtWidgets import QWidget, QLineEdit, QVBoxLayout

if TYPE_CHECKING:
    from core.bus import EventBus

logger = logging.getLogger("B.ui.input")

BG_COLOR = QColor("#1A1A1A")
TEXT_COLOR = "#00E6FF"
CORNER_RADIUS = 12

_HAS_WIN32 = False
if sys.platform == "win32":
    try:
        import win32con
        import win32gui
        _HAS_WIN32 = True
    except ImportError:
        pass

class InputBox(QWidget):
    def __init__(self, bus: EventBus, screen_rect: QRect) -> None:
        super().__init__()
        self._bus = bus
        self._screen_rect = screen_rect
        self._speak_mode_active = False

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setStyleSheet("background: transparent;")
        
        w, h = 400, 50
        self.setFixedSize(w, h)

        # Center at bottom of screen
        x = screen_rect.left() + (screen_rect.width() - w) // 2
        y = screen_rect.bottom() - h - 60
        self.move(x, y)

        path = QPainterPath()
        from PyQt6.QtCore import QRectF
        path.addRoundedRect(QRectF(0, 0, w, h), CORNER_RADIUS, CORNER_RADIUS)
        mask = QRegion(path.toFillPolygon(QTransform()).toPolygon())
        self.setMask(mask)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 5, 10, 5)

        self._line_edit = QLineEdit()
        self._line_edit.setStyleSheet(f"""
            QLineEdit {{
                background: transparent;
                color: {TEXT_COLOR};
                border: none;
                selection-background-color: #333333;
            }}
        """)
        font = QFont()
        font.setPointSize(12)
        self._line_edit.setFont(font)
        self._line_edit.setPlaceholderText("Talk to B...")
        self._line_edit.returnPressed.connect(self._on_enter)
        self._line_edit.installEventFilter(self)
        
        layout.addWidget(self._line_edit)
        
        self._bus.subscribe("b_speak_mode_toggled", self._on_speak_mode_toggled)
        self._bus.subscribe("user_hearing", self._on_user_hearing)
        self._bus.subscribe("user_spoke", self._on_user_spoke_voice)
        
        self.hide()
        logger.info("InputBox initialized")

    def toggle(self) -> None:
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.activateWindow()
            self._line_edit.setFocus()
            self._line_edit.clear()

    def _on_speak_mode_toggled(self, payload: dict) -> None:
        self._speak_mode_active = payload.get("active", False)
        if self._speak_mode_active:
            self._line_edit.setPlaceholderText(" Speak Mode Active - Listening...")
            self._line_edit.clear()
            self.show()
            self.activateWindow()
        else:
            self._line_edit.setPlaceholderText("Talk to B...")
            self._line_edit.clear()
            self.hide()

    def _on_user_hearing(self, payload: dict) -> None:
        """Partial results while user is still talking."""
        text = payload.get("text", "")
        if text:
            logger.info("InputBox received user_hearing: '%s'", text)
            # Marshal to main thread since this comes from the background EarsSensor thread
            QTimer.singleShot(0, lambda: self._update_ui_text(text))

    def _update_ui_text(self, text: str) -> None:
        if self._speak_mode_active:
            self._line_edit.setText(f" {text}")
        else:
            self._line_edit.setText(text)
            
        if not self.isVisible():
            self.show()

    def _on_user_spoke_voice(self, payload: dict) -> None:
        """Final recognition result."""
        if payload.get("source") != "voice":
            return
        
        text = payload.get("text", "")
        # Marshal to main thread
        QTimer.singleShot(0, lambda: self._finalize_ui_text(text))

    def _finalize_ui_text(self, text: str) -> None:
        if self._speak_mode_active:
            self._line_edit.setText(f" {text}")
        else:
            self._line_edit.setText(text)
        self.show()
        
        # In speak mode, we clear the text but KEEP the box visible
        if self._speak_mode_active:
            QTimer.singleShot(2000, self._line_edit.clear)
        else:
            # Keep the final text visible for a second then hide
            QTimer.singleShot(1500, self.hide)
            QTimer.singleShot(1600, self._line_edit.clear)

    def _on_enter(self) -> None:
        text = self._line_edit.text().strip()
        if text:
            self._bus.publish("user_spoke", {"text": text})
            logger.info("User spoke: %s", text)
        self.hide()

    def eventFilter(self, obj, event) -> bool:
        if obj == self._line_edit and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Escape:
                self.hide()
                return True
        return super().eventFilter(obj, event)

    def _enforce_topmost(self) -> None:
        if not _HAS_WIN32:
            return
        hwnd = int(self.winId())
        win32gui.SetWindowPos(
            hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
            win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE
        )

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._enforce_topmost()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), BG_COLOR)
        painter.end()
        # logger.debug("InputBox painted")
