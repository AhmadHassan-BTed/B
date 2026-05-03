"""
ui/chat.py — B's Voice Bubble
═════════════════════════════

A floating, transparent window that displays B's spoken text.
It follows B around and fades out automatically.
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QRect, QPoint, pyqtSignal
from PyQt6.QtGui import QPainter, QPaintEvent, QPainterPath, QColor, QFont, QFontMetrics, QRegion, QTransform
from PyQt6.QtWidgets import QWidget, QLabel, QVBoxLayout, QGraphicsOpacityEffect
from ui.theme import GET_PALETTE

if TYPE_CHECKING:
    from core.bus import EventBus

logger = logging.getLogger("B.ui.chat")

_HAS_WIN32 = False
if sys.platform == "win32":
    try:
        import win32con
        import win32gui
        _HAS_WIN32 = True
    except ImportError:
        pass

# Constants
BUBBLE_BG = QColor("#1A1A1A")
TEXT_COLOR = "#00E6FF" # Cyan
CORNER_RADIUS = 12
MAX_WIDTH = 280
PADDING = 12
Y_OFFSET = 8 # Gap below B's face
FADE_DELAY_MS = 4000
FADE_DURATION_MS = 1000

class ChatBubble(QWidget):
    _show_signal = pyqtSignal(str, str)
    _fade_signal = pyqtSignal()
    _thinking_signal = pyqtSignal()
    
    def __init__(self, bus: 'EventBus', screen_rect: QRect) -> None:
        super().__init__()
        self._bus = bus
        self._screen_rect = screen_rect
        self._b_pos = QPoint(0, 0)
        self._b_size = QPoint(180, 112) # B's canvas size
        self._current_emotion = "neutral"

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setStyleSheet("background: transparent;")

        # Layout and Label
        layout = QVBoxLayout(self)
        layout.setContentsMargins(PADDING, PADDING, PADDING, PADDING)
        self._label = QLabel("")
        self._label.setStyleSheet(f"color: {TEXT_COLOR};")
        font = QFont()
        font.setPointSize(11)
        self._label.setFont(font)
        self._label.setWordWrap(True)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._label)

        # Opacity effect for fading
        self._opacity_effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity_effect)
        self._opacity_effect.setOpacity(0.0)

        # Animations and Timers
        self._fade_anim = QPropertyAnimation(self._opacity_effect, b"opacity")
        self._fade_anim.setDuration(FADE_DURATION_MS)
        self._fade_anim.finished.connect(self.hide)
        self._fade_timer = QTimer(self)
        self._fade_timer.setSingleShot(True)
        self._fade_timer.timeout.connect(self._start_fade_out)
        
        self._fade_signal.connect(self._do_start_fade_timer)
        self._show_signal.connect(self._do_show_text)
        self._thinking_signal.connect(self._do_thinking)

        self._bus.subscribe("b_playing_sentence", self._on_b_playing_sentence, priority=50)
        self._bus.subscribe("b_finished_speaking", self._on_b_finished_speaking, priority=50)
        self._bus.subscribe("b_thinking", self._on_b_thinking, priority=50)
        self._bus.subscribe("position_updated", self._on_position_updated, priority=50)

        self.hide() # Hidden initially
        # logger.info("ChatBubble initialized")

    def initialize(self) -> None:
        self._apply_click_through()

    def _apply_click_through(self) -> None:
        if not _HAS_WIN32:
            return
        hwnd = int(self.winId())
        style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        style |= win32con.WS_EX_LAYERED | win32con.WS_EX_TRANSPARENT
        win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, style)

        import ctypes
        try:
            dwm = ctypes.windll.dwmapi
            val = ctypes.c_int(1)
            dwm.DwmSetWindowAttribute(hwnd, 2, ctypes.byref(val), 4)
            val2 = ctypes.c_int(3)
            dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(val2), 4)
        except Exception:
            pass
        self._enforce_topmost()

    def _enforce_topmost(self) -> None:
        if not _HAS_WIN32:
            return
        hwnd = int(self.winId())
        win32gui.SetWindowPos(
            hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
            win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE
        )

    def _on_b_playing_sentence(self, payload: dict) -> None:
        text = payload.get("text", "")
        emotion = payload.get("emotion", "neutral")
        if text:
            self._show_signal.emit(text, emotion)
            
    def _do_show_text(self, text: str, emotion: str) -> None:
        self._current_emotion = emotion
        
        # Update text color and bold state based on emotion
        core_color, _, _ = GET_PALETTE(emotion)
        bold = (emotion == "angry")
        
        self._label.setStyleSheet(f"color: {core_color.name()};")
        font = self._label.font()
        font.setBold(bold)
        self._label.setFont(font)
        
        self._show_text(text)
        self.update() # Trigger repaint for border color change
            
    def _on_b_finished_speaking(self, payload: dict) -> None:
        # logger.info("ChatBubble received b_finished_speaking. Emitting fade_signal.")
        # Emit signal to ensure we start the QTimer on the main GUI thread!
        self._fade_signal.emit()
        
    def _do_start_fade_timer(self) -> None:
        # logger.info("ChatBubble _do_start_fade_timer called. Starting 2000ms timer.")
        # Start fading out 2 seconds after the voice finishes
        if self._label.text() != "...":
            self._fade_timer.start(2000)

    def _on_b_thinking(self, payload: dict) -> None:
        self._thinking_signal.emit()

    def _do_thinking(self) -> None:
        self._fade_timer.stop()
        self._show_text("...")

    def _show_text(self, text: str) -> None:
        self._label.setText(text)
        
        # Calculate size based on text
        fm = QFontMetrics(self._label.font())
        rect = fm.boundingRect(0, 0, MAX_WIDTH - 2*PADDING, 0, Qt.TextFlag.TextWordWrap, text)
        w = min(MAX_WIDTH, rect.width() + 2*PADDING + 10)
        h = rect.height() + 2*PADDING + 10
        self.setFixedSize(w, h)

        # Apply rounded mask
        path = QPainterPath()
        from PyQt6.QtCore import QRectF
        path.addRoundedRect(QRectF(0, 0, w, h), CORNER_RADIUS, CORNER_RADIUS)
        mask = QRegion(path.toFillPolygon(QTransform()).toPolygon())
        self.setMask(mask)

        self._update_position()

        self._fade_anim.stop()
        self._opacity_effect.setOpacity(1.0)
        self.show()

    def _start_fade_out(self) -> None:
        # logger.info("ChatBubble _start_fade_out called! Animating opacity to 0.0")
        self._fade_anim.setStartValue(1.0)
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.start()
        # Connect finished signal to hide? Usually setting opacity 0 is enough, but hiding is cleaner.
        # The tool window will just be fully transparent.

    def _on_position_updated(self, payload: dict) -> None:
        self._b_pos = QPoint(int(payload["x"]), int(payload["y"]))
        if self.isVisible() and self._opacity_effect.opacity() > 0:
            self._update_position()

    def _update_position(self) -> None:
        # Center horizontally below B
        x = self._b_pos.x() + (self._b_size.x() // 2) - (self.width() // 2)
        y = self._b_pos.y() + self._b_size.y() + Y_OFFSET

        # Clamp to screen
        if x < self._screen_rect.left():
            x = self._screen_rect.left()
        if x + self.width() > self._screen_rect.right():
            x = self._screen_rect.right() - self.width()
        
        if y + self.height() > self._screen_rect.bottom():
            # If it goes off bottom, put it ABOVE B
            y = self._b_pos.y() - self.height() - Y_OFFSET

        self.move(x, y)
        self._enforce_topmost()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        
        # Draw background
        painter.fillRect(self.rect(), BUBBLE_BG)
        
        # Draw colored border based on emotion
        border_color, _, _ = GET_PALETTE(self._current_emotion)
        from PyQt6.QtGui import QPen
        pen = QPen(border_color, 3)
        painter.setPen(pen)
        
        # Draw border inside the rect
        path = QPainterPath()
        from PyQt6.QtCore import QRectF
        rect = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        path.addRoundedRect(rect, CORNER_RADIUS, CORNER_RADIUS)
        painter.drawPath(path)
        
        painter.end()
