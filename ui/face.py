"""
ui/face.py — B's Face: Morph Engine
════════════════════════════════════

True shape morphing — NOT crossfade.

When B changes emotion, his eyes don't "fade out and fade in."
They MORPH: the current shape squishes down to a thin line, then
the new shape bounces out from that line with elastic spring energy.

Two-phase animation:
    Phase 1 (t=0.0→0.5): Current eyes SQUISH vertically (scale Y: 1.0→0.0)
    Phase 2 (t=0.5→1.0): New eyes EXPAND with elastic bounce (scale Y: 0.0→1.0+overshoot)

This looks like B is "blinking into" a new expression — organic,
cute, and alive. One shape at a time, no overlapping ghosts.
"""

from __future__ import annotations

import logging
import math
import time
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPainter, QPaintEvent
from PyQt6.QtWidgets import QWidget

from ui import expressions
from ui.theme import CANVAS_H, CANVAS_W, TRANSITION_SECS, VISOR_BG

if TYPE_CHECKING:
    from core.bus import EventBus

logger = logging.getLogger("B.ui.face")


class FaceRenderer(QWidget):
    _emotion_signal = pyqtSignal(str)

    def __init__(self, bus: EventBus, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._bus = bus

        self._emotion: str = "neutral"
        self._prev_emotion: str = "neutral"

        # Transition: 1.0 = fully arrived at current emotion
        self._t: float = 1.0
        self._t_start: float = 0.0
        self._is_blink: bool = False

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(CANVAS_W, CANVAS_H)

        self._bus.subscribe("emotion_changed", self._on_emotion_changed, priority=50)
        self._bus.subscribe("tick", self._on_tick, priority=55)
        self._bus.subscribe("b_playing_sentence", self._on_b_playing_sentence, priority=50)
        self._bus.subscribe("b_finished_speaking", self._on_b_finished_speaking, priority=50)
        
        # Talking & Float state
        self._is_talking: bool = False
        self._jitter_y: float = 0.0
        self._jitter_scale: float = 1.0
        self._float_x: float = 0.0
        self._float_y: float = 0.0
        self._pulse_t: float = 0.0
        
        self._emotion_signal.connect(self._do_emotion_change)
        
        logger.info("FaceRenderer initialized (%dx%d, morph transitions)", CANVAS_W, CANVAS_H)

    def _on_emotion_changed(self, payload: dict) -> None:
        new = payload.get("emotion", "neutral")
        self._emotion_signal.emit(new)

    def _do_emotion_change(self, new: str) -> None:
        # We always trigger the morph transition, even if the emotion is the same,
        # to provide visual feedback that B is 're-expressing' himself for the new sentence.
        self._prev_emotion = self._emotion
        self._emotion = new
        self._t = 0.0
        self._t_start = time.monotonic()
        self._is_blink = (new == "blink" or self._prev_emotion == "blink")

    def _on_b_playing_sentence(self, payload: dict) -> None:
        self._is_talking = True

    def _on_b_finished_speaking(self, payload: dict) -> None:
        self._is_talking = False
        self._jitter_y = 0.0
        self._jitter_scale = 1.0

    def _on_tick(self, payload: dict) -> None:
        now = time.monotonic()
        if self._t < 1.0:
            elapsed = now - self._t_start
            duration = TRANSITION_SECS / 3.0 if self._is_blink else TRANSITION_SECS
            self._t = min(1.0, elapsed / duration)
        
        # Procedural "Eye Float"
        self._float_x = math.sin(now * 0.5) * 1.5
        self._float_y = math.cos(now * 0.8) * 1.0
        
        # Talking Jitter (Vertical only, no scale jitter as per user request)
        if self._is_talking:
            self._pulse_t += payload.get("dt", 0.016) * 15.0
            self._jitter_y = math.sin(self._pulse_t) * 1.2
            self._jitter_scale = 1.0 # Fixed scale while talking
        else:
            self._jitter_y = 0.0
            self._jitter_scale = 1.0
            
        self.update()

    # ──────────────────────────────────────────────────────────────────
    # Easing
    # ──────────────────────────────────────────────────────────────────

    @staticmethod
    def _ease_in_quad(t: float) -> float:
        """Accelerating squish — starts slow, finishes fast."""
        return t * t

    @staticmethod
    def _ease_smooth(t: float) -> float:
        """Smooth ease-out — decelerates naturally. Used for blinks."""
        return 1.0 - (1.0 - t) * (1.0 - t)

    @staticmethod
    def _ease_elastic_out(t: float) -> float:
        """Elastic bounce — overshoots and wobbles. Used for mood changes."""
        if t <= 0.0:
            return 0.0
        if t >= 1.0:
            return 1.0
        p = 0.35
        return pow(2.0, -10.0 * t) * math.sin((t - p / 4.0) * (2.0 * math.pi) / p) + 1.0

    # ──────────────────────────────────────────────────────────────────
    # Paint
    # ──────────────────────────────────────────────────────────────────

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # Black visor background
        painter.fillRect(self.rect(), VISOR_BG)

        if self._t >= 1.0:
            self._draw(painter, self._emotion, scale_x=1.0, scale_y=1.0)
        elif self._is_blink:
            # ──────────────────────────────────────────────────────────
            # BLINK: Quick, natural close-and-open. No elastic bounce.
            # Just a smooth vertical squish and expand.
            # ──────────────────────────────────────────────────────────
            if self._t < 0.5:
                phase = self._t / 0.5
                sy = 1.0 - self._ease_smooth(phase)
                self._draw(painter, self._prev_emotion, scale_x=1.0, scale_y=max(0.05, sy))
            else:
                phase = (self._t - 0.5) / 0.5
                sy = self._ease_smooth(phase)
                self._draw(painter, self._emotion, scale_x=1.0, scale_y=max(0.05, sy))
        else:
            # ──────────────────────────────────────────────────────────
            # MOOD MORPH: Bouncy squish-and-bloom with elastic spring.
            # ──────────────────────────────────────────────────────────
            if self._t < 0.5:
                phase = self._t / 0.5
                squish = self._ease_in_quad(phase)
                sy = 1.0 - squish
                sx = 1.0 + squish * 0.15
                self._draw(painter, self._prev_emotion, scale_x=sx, scale_y=max(0.01, sy))
            else:
                phase = (self._t - 0.5) / 0.5
                bloom = self._ease_elastic_out(phase)
                sy = bloom
                sx = 1.0 + (1.0 - bloom) * 0.1
                self._draw(painter, self._emotion, scale_x=sx, scale_y=max(0.01, sy))

        painter.end()

    def _draw(self, painter: QPainter, emotion: str, scale_x: float, scale_y: float) -> None:
        """Draw an emotion with scale transform centered on the eye line."""
        # Update the theme colors for this specific emotion before drawing
        expressions.set_emotion_theme(emotion)
        
        painter.save()

        # Scale from the horizontal center and vertical eye center
        # so the squish/bloom feels centered on the eyes, not the visor
        cx = CANVAS_W / 2.0 + self._float_x
        cy = CANVAS_H / 2.0 + self._float_y + self._jitter_y
        painter.translate(cx, cy)
        painter.scale(scale_x * self._jitter_scale, scale_y * self._jitter_scale)
        painter.translate(-cx, -cy)

        draw_fn = getattr(expressions, f"draw_{emotion}", expressions.draw_neutral)
        draw_fn(painter)

        painter.restore()
