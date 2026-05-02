"""
ui/expressions.py — B's Emotional Vocabulary
═════════════════════════════════════════════

Each function draws one emotion on a QPainter. Pure rendering —
no state, no timers, no transitions. Just "given a painter, draw
these eyes."

Eye-only expressions — all emotion conveyed through eye shape,
size, tilt, glow intensity, and positioning alone. No mouth.

Glow helpers and shape builders live here too since they're only
used by the expression drawing functions.

Every draw_* function has the same signature:
draw_neutral(painter: QPainter) -> None
"""

from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QPainter, QPainterPath, QPen

from ui.theme import (
    CYAN, GLOW, GET_PALETTE,
    LEFT_CX, RIGHT_CX, EYE_CY,
)

# ──────────────────────────────────────────────────────────────
# Global palette state — updated by FaceRenderer before drawing
# ──────────────────────────────────────────────────────────────
EYE_COLOR, EYE_MID, EYE_DIM = GET_PALETTE("default")

def set_emotion_theme(emotion: str) -> None:
    """Updates the global drawing palette based on the emotion."""
    global EYE_COLOR, EYE_MID, EYE_DIM
    EYE_COLOR, EYE_MID, EYE_DIM = GET_PALETTE(emotion)

# ══════════════════════════════════════════════════════════════════════
# GLOW HELPERS — 3-pass rendering for the neon bloom effect
# ══════════════════════════════════════════════════════════════════════

def _glow_rrect(p: QPainter, rect: QRectF, r: float) -> None:
    """Rounded rectangle with glow."""
    g = GLOW
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(EYE_DIM))
    p.drawRoundedRect(rect.adjusted(-g, -g, g, g), r + 3, r + 3)
    p.setBrush(QBrush(EYE_MID))
    p.drawRoundedRect(rect.adjusted(-g // 2, -g // 2, g // 2, g // 2), r + 1, r + 1)
    p.setBrush(QBrush(EYE_COLOR))
    p.drawRoundedRect(rect, r, r)


def _glow_arc(p: QPainter, rect: QRectF, start: float, span: float, thick: float = 8.0) -> None:
    """Thick arc with glow. Angles in degrees."""
    p.setBrush(Qt.BrushStyle.NoBrush)
    s16, sp16 = int(start * 16), int(span * 16)
    p.setPen(QPen(EYE_DIM, thick + GLOW * 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawArc(rect, s16, sp16)
    p.setPen(QPen(EYE_MID, thick + GLOW, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawArc(rect, s16, sp16)
    p.setPen(QPen(EYE_COLOR, thick, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawArc(rect, s16, sp16)


def _glow_ring(p: QPainter, cx: float, cy: float, ro: float, ri: float) -> None:
    """Ring (donut) with glow."""
    g = GLOW
    p.setPen(Qt.PenStyle.NoPen)
    for color, inflate in [(EYE_DIM, g), (EYE_MID, g // 2), (EYE_COLOR, 0)]:
        outer = QPainterPath()
        outer.addEllipse(QPointF(cx, cy), ro + inflate, ro + inflate)
        inner = QPainterPath()
        inner.addEllipse(QPointF(cx, cy), max(1, ri - inflate // 2), max(1, ri - inflate // 2))
        p.setBrush(QBrush(color))
        p.drawPath(outer - inner)


def _glow_path(p: QPainter, path: QPainterPath) -> None:
    """Arbitrary filled path with glow bloom."""
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(EYE_DIM, GLOW * 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.drawPath(path)
    p.setPen(QPen(EYE_MID, GLOW, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.drawPath(path)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(EYE_COLOR))
    p.drawPath(path)


def _glow_stroke(p: QPainter, path: QPainterPath, thick: float = 4.0) -> None:
    """Arbitrary stroked (unfilled) path with glow — for spirals, lines."""
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(EYE_DIM, thick + GLOW * 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.drawPath(path)
    p.setPen(QPen(EYE_MID, thick + GLOW, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.drawPath(path)
    p.setPen(QPen(EYE_COLOR, thick, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.drawPath(path)


def _glow_dot(p: QPainter, cx: float, cy: float, r: float) -> None:
    """Filled glowing circle — for highlights and pupils."""
    g = GLOW
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(EYE_DIM))
    p.drawEllipse(QPointF(cx, cy), r + g, r + g)
    p.setBrush(QBrush(EYE_MID))
    p.drawEllipse(QPointF(cx, cy), r + g // 2, r + g // 2)
    p.setBrush(QBrush(EYE_COLOR))
    p.drawEllipse(QPointF(cx, cy), r, r)


# ══════════════════════════════════════════════════════════════════════
# SHAPE BUILDERS — Reusable complex paths
# ══════════════════════════════════════════════════════════════════════

def _make_star(cx: float, cy: float, outer_r: float, inner_r: float) -> QPainterPath:
    """Five-pointed star."""
    path = QPainterPath()
    pts = []
    for i in range(10):
        a = -math.pi / 2 + i * math.pi / 5
        r = outer_r if i % 2 == 0 else inner_r
        pts.append(QPointF(cx + r * math.cos(a), cy + r * math.sin(a)))
    path.moveTo(pts[0])
    for pt in pts[1:]:
        path.lineTo(pt)
    path.closeSubpath()
    return path


def _make_heart(cx: float, cy: float, s: float) -> QPainterPath:
    """Heart shape — elongated vertically while maintaining width."""
    path = QPainterPath()
    path.moveTo(cx, cy + s * 1.1)
    path.cubicTo(cx - s * 1.3, cy + s * 0.1, cx - s * 1.1, cy - s * 1.0, cx, cy - s * 0.4)
    path.cubicTo(cx + s * 1.1, cy - s * 1.0, cx + s * 1.3, cy + s * 0.1, cx, cy + s * 1.1)
    return path


def _make_teardrop(cx: float, cy: float, s: float) -> QPainterPath:
    """Teardrop / falling tear."""
    path = QPainterPath()
    path.moveTo(cx, cy - s * 0.6)
    path.cubicTo(cx - s * 0.5, cy, cx - s * 0.4, cy + s * 0.5, cx, cy + s * 0.6)
    path.cubicTo(cx + s * 0.4, cy + s * 0.5, cx + s * 0.5, cy, cx, cy - s * 0.6)
    return path


def _make_angry_eye(cx: float, cy: float, w: float, h: float, flip: bool = False) -> QPainterPath:
    """
    Rectangular eye with a sharp diagonal cut at the inner top corner,
    creating an aggressive downward scowl. flip=True mirrors for the right eye.
    """
    path = QPainterPath()
    hw, hh = w / 2, h / 2
    cut = h * 0.5          # How deep the angled slice cuts inward
    corner_r = 3.0

    if not flip:
        # Left eye: inner-top corner (right side) is angled down
        path.moveTo(cx - hw + corner_r, cy - hh)
        path.quadTo(cx - hw, cy - hh, cx - hw, cy - hh + corner_r)
        path.lineTo(cx - hw, cy + hh - corner_r)
        path.quadTo(cx - hw, cy + hh, cx - hw + corner_r, cy + hh)
        path.lineTo(cx + hw - corner_r, cy + hh)
        path.quadTo(cx + hw, cy + hh, cx + hw, cy + hh - corner_r)
        path.lineTo(cx + hw, cy - hh + cut)      # <- sharp angled top-right
        path.lineTo(cx - hw + corner_r, cy - hh)
    else:
        # Right eye: inner-top corner (left side) is angled down
        path.moveTo(cx - hw, cy - hh + cut)      # <- sharp angled top-left
        path.lineTo(cx + hw - corner_r, cy - hh)
        path.quadTo(cx + hw, cy - hh, cx + hw, cy - hh + corner_r)
        path.lineTo(cx + hw, cy + hh - corner_r)
        path.quadTo(cx + hw, cy + hh, cx + hw - corner_r, cy + hh)
        path.lineTo(cx - hw + corner_r, cy + hh)
        path.quadTo(cx - hw, cy + hh, cx - hw, cy + hh - corner_r)
        path.lineTo(cx - hw, cy - hh + cut)

    path.closeSubpath()
    return path


def _make_spiral(cx: float, cy: float, turns: int = 2, max_r: float = 14.0) -> QPainterPath:
    """Outward spiral — for dizzy/overwhelmed expressions."""
    path = QPainterPath()
    steps = turns * 48
    for i in range(steps + 1):
        t = i / steps
        angle = t * turns * 2 * math.pi - math.pi / 2
        r = t * max_r
        x = cx + r * math.cos(angle)
        y = cy + r * math.sin(angle)
        if i == 0:
            path.moveTo(x, y)
        else:
            path.lineTo(x, y)
    return path


def _make_lightning(cx: float, cy: float, s: float) -> QPainterPath:
    """Zigzag lightning bolt — for electric/energised expression."""
    path = QPainterPath()
    path.moveTo(cx + s * 0.2, cy - s)
    path.lineTo(cx - s * 0.3, cy - s * 0.1)
    path.lineTo(cx + s * 0.15, cy - s * 0.1)
    path.lineTo(cx - s * 0.2, cy + s)
    path.lineTo(cx + s * 0.3, cy + s * 0.1)
    path.lineTo(cx - s * 0.1, cy + s * 0.1)
    path.closeSubpath()
    return path

def _make_z(cx: float, cy: float, s: float) -> QPainterPath:
    """Z shape for sleeping."""
    path = QPainterPath()
    path.moveTo(cx - s / 2, cy - s / 2)
    path.lineTo(cx + s / 2, cy - s / 2)
    path.lineTo(cx - s / 2, cy + s / 2)
    path.lineTo(cx + s / 2, cy + s / 2)
    return path


# ══════════════════════════════════════════════════════════════════════
# EMOTION DRAW FUNCTIONS — One per emotion, all same signature
# ══════════════════════════════════════════════════════════════════════

def draw_neutral(p: QPainter) -> None:
    """Two balanced rounded squares — at rest, observing."""
    w, h, r = 30, 28, 5
    _glow_rrect(p, QRectF(LEFT_CX - w / 2, EYE_CY - h / 2, w, h), r)
    _glow_rrect(p, QRectF(RIGHT_CX - w / 2, EYE_CY - h / 2, w, h), r)


def draw_happy(p: QPainter) -> None:
    """Large upward crescents — ^^ eyes, thick and bright."""
    aw, ah = 36, 30
    _glow_arc(p, QRectF(LEFT_CX - aw / 2, EYE_CY - ah / 2, aw, ah), 0, 180, 11)
    _glow_arc(p, QRectF(RIGHT_CX - aw / 2, EYE_CY - ah / 2, aw, ah), 0, 180, 11)


def draw_sad(p: QPainter) -> None:
    """Drooping downward crescents — vv eyes, softer and lower."""
    aw, ah = 30, 24
    _glow_arc(p, QRectF(LEFT_CX - aw / 2, EYE_CY - ah / 6, aw, ah), 180, 180, 8)
    _glow_arc(p, QRectF(RIGHT_CX - aw / 2, EYE_CY - ah / 6, aw, ah), 180, 180, 8)


def draw_angry(p: QPainter) -> None:
    """Custom angular shapes with inner scowl cuts — sharp and menacing."""
    _glow_path(p, _make_angry_eye(LEFT_CX, EYE_CY, 30, 22, flip=False))
    _glow_path(p, _make_angry_eye(RIGHT_CX, EYE_CY, 30, 22, flip=True))


def draw_surprised(p: QPainter) -> None:
    """Large wide-open rings — O_O shock."""
    _glow_ring(p, LEFT_CX, EYE_CY, 19, 9)
    _glow_ring(p, RIGHT_CX, EYE_CY, 19, 9)


def draw_winking(p: QPainter) -> None:
    """Left eye fully open, right eye flat closed line."""
    _glow_rrect(p, QRectF(LEFT_CX - 15, EYE_CY - 14, 30, 28), 5)
    _glow_rrect(p, QRectF(RIGHT_CX - 15, EYE_CY - 3, 30, 6), 3)


def draw_wink_left(p: QPainter) -> None:
    """Left eye closed flat, right eye fully open."""
    _glow_rrect(p, QRectF(LEFT_CX - 15, EYE_CY - 3, 30, 6), 3)
    _glow_rrect(p, QRectF(RIGHT_CX - 15, EYE_CY - 14, 30, 28), 5)


def draw_sleeping(p: QPainter) -> None:
    """Both eyes closed with gentle bottom-arc lids + glowing Zzz."""
    aw, ah = 30, 22
    _glow_arc(p, QRectF(LEFT_CX - aw / 2, EYE_CY - ah / 4, aw, ah), 0, 180, 9)
    _glow_arc(p, QRectF(RIGHT_CX - aw / 2, EYE_CY - ah / 4, aw, ah), 0, 180, 9)
    
    # Rising Zzz instead of dots
    _glow_stroke(p, _make_z(RIGHT_CX + 28, EYE_CY - 15, 6), thick=2.5)
    _glow_stroke(p, _make_z(RIGHT_CX + 42, EYE_CY - 32, 9), thick=3.0)


def draw_confused(p: QPainter) -> None:
    """One normal eye, one strongly tilted — processing something wrong."""
    _glow_rrect(p, QRectF(LEFT_CX - 16, EYE_CY - 14, 32, 28), 5)
    p.save()
    p.translate(RIGHT_CX, EYE_CY)
    p.rotate(28)
    _glow_rrect(p, QRectF(-12, -12, 24, 24), 4)
    p.restore()


def draw_laughing(p: QPainter) -> None:
    """Extremely squished happy crescents — eyes nearly shut from laughter."""
    aw, ah = 38, 16
    _glow_arc(p, QRectF(LEFT_CX - aw / 2, EYE_CY, aw, ah), 0, 180, 11)
    _glow_arc(p, QRectF(RIGHT_CX - aw / 2, EYE_CY, aw, ah), 0, 180, 11)


def draw_confident(p: QPainter) -> None:
    """Narrow horizontal squint — cool, collected, slightly smug."""
    w, h = 30, 11
    _glow_rrect(p, QRectF(LEFT_CX - w / 2, EYE_CY - h / 2, w, h), 4)
    _glow_rrect(p, QRectF(RIGHT_CX - w / 2, EYE_CY - h / 2, w, h), 4)


def draw_crying(p: QPainter) -> None:
    """Sad drooping arcs + two falling teardrops — one on each side."""
    aw, ah = 26, 18
    _glow_arc(p, QRectF(LEFT_CX - aw / 2, EYE_CY - ah / 6, aw, ah), 180, 180, 7)
    _glow_arc(p, QRectF(RIGHT_CX - aw / 2, EYE_CY - ah / 6, aw, ah), 180, 180, 7)
    _glow_path(p, _make_teardrop(LEFT_CX - 2, EYE_CY + 18, 8))
    _glow_path(p, _make_teardrop(RIGHT_CX + 2, EYE_CY + 18, 9))


def draw_playful(p: QPainter) -> None:
    """Two squares tilted in opposite directions — bouncy asymmetry."""
    p.save()
    p.translate(LEFT_CX, EYE_CY)
    p.rotate(-15)
    _glow_rrect(p, QRectF(-13, -13, 26, 26), 4)
    p.restore()
    p.save()
    p.translate(RIGHT_CX, EYE_CY)
    p.rotate(15)
    _glow_rrect(p, QRectF(-15, -15, 30, 30), 5)
    p.restore()


def draw_star_struck(p: QPainter) -> None:
    """Glowing five-pointed stars — totally dazzled."""
    _glow_path(p, _make_star(LEFT_CX, EYE_CY, 18, 8))
    _glow_path(p, _make_star(RIGHT_CX, EYE_CY, 18, 8))


def draw_bored(p: QPainter) -> None:
    """Ultra-thin horizontal strips — half-lidded total disinterest."""
    _glow_rrect(p, QRectF(LEFT_CX - 15, EYE_CY - 4, 30, 8), 4)
    _glow_rrect(p, QRectF(RIGHT_CX - 15, EYE_CY - 4, 30, 8), 4)


def draw_love_struck(p: QPainter) -> None:
    """Glowing heart eyes — completely smitten. Shifted up with bold outlines."""
    offset_y = EYE_CY - 8
    path_l = _make_heart(LEFT_CX, offset_y, 26)
    path_r = _make_heart(RIGHT_CX, offset_y, 26)
    
    # 1. Fill with glow
    _glow_path(p, path_l)
    _glow_path(p, path_r)
    
    # 2. Add bold stroke for extra 'pop'
    _glow_stroke(p, path_l, thick=6.0)
    _glow_stroke(p, path_r, thick=6.0)


def draw_focused(p: QPainter) -> None:
    """Slightly different-sized eyes, narrowed — deep concentration."""
    _glow_rrect(p, QRectF(LEFT_CX - 14, EYE_CY - 12, 28, 24), 4)
    _glow_rrect(p, QRectF(RIGHT_CX - 11, EYE_CY - 9, 22, 18), 4)


def draw_blink(p: QPainter) -> None:
    """Ultra-thin lines — momentary closure mid-blink."""
    _glow_rrect(p, QRectF(LEFT_CX - 15, EYE_CY - 2, 30, 4), 2)
    _glow_rrect(p, QRectF(RIGHT_CX - 15, EYE_CY - 2, 30, 4), 2)


def draw_curious(p: QPainter) -> None:
    """One square eye, one raised and tilted — inquisitive head-tilt energy."""
    _glow_rrect(p, QRectF(LEFT_CX - 14, EYE_CY - 14, 28, 28), 6)
    p.save()
    p.translate(RIGHT_CX, EYE_CY - 5)   # shifted up to suggest raised brow
    p.rotate(-14)
    _glow_rrect(p, QRectF(-15, -15, 30, 30), 8)
    p.restore()


def draw_excited(p: QPainter) -> None:
    """Extra-large bright squares + inner highlight dots — wide-eyed energy."""
    w, h = 36, 36
    _glow_rrect(p, QRectF(LEFT_CX - w / 2, EYE_CY - h / 2, w, h), 9)
    _glow_rrect(p, QRectF(RIGHT_CX - w / 2, EYE_CY - h / 2, w, h), 9)
    _glow_dot(p, LEFT_CX + 9, EYE_CY - 9, 4)
    _glow_dot(p, RIGHT_CX + 9, EYE_CY - 9, 4)


def draw_shy(p: QPainter) -> None:
    """Eyes shifted downward and slightly inward — avoidant gaze."""
    w, h = 24, 18
    _glow_rrect(p, QRectF(LEFT_CX - w / 2 + 2, EYE_CY + 7, w, h), 5)
    _glow_rrect(p, QRectF(RIGHT_CX - w / 2 - 2, EYE_CY + 7, w, h), 5)


def draw_skeptical(p: QPainter) -> None:
    """One fully open eye, one paper-thin line — classic deadpan side-eye."""
    _glow_rrect(p, QRectF(LEFT_CX - 15, EYE_CY - 14, 30, 28), 5)
    _glow_rrect(p, QRectF(RIGHT_CX - 15, EYE_CY - 3, 30, 5), 2)


def draw_thinking(p: QPainter) -> None:
    """Both eyes shifted upward and slightly right — processing, looking away."""
    w, h = 26, 24
    _glow_rrect(p, QRectF(LEFT_CX - w / 2 + 2, EYE_CY - h / 2 - 8, w, h), 4)
    _glow_rrect(p, QRectF(RIGHT_CX - w / 2 + 5, EYE_CY - h / 2 - 8, w, h), 4)


def draw_delighted(p: QPainter) -> None:
    """Bigger, brighter, thicker happy crescents — overjoyed."""
    aw, ah = 36, 30
    _glow_arc(p, QRectF(LEFT_CX - aw / 2, EYE_CY - ah / 2, aw, ah), 0, 180, 13)
    _glow_arc(p, QRectF(RIGHT_CX - aw / 2, EYE_CY - ah / 2, aw, ah), 0, 180, 13)


def draw_pouting(p: QPainter) -> None:
    """Both eyes angled inward at the top — frustrated, pouty furrow."""
    w, h = 24, 22
    p.save()
    p.translate(LEFT_CX, EYE_CY)
    p.rotate(-12)
    _glow_rrect(p, QRectF(-w / 2, -h / 2, w, h), 4)
    p.restore()
    p.save()
    p.translate(RIGHT_CX, EYE_CY)
    p.rotate(12)
    _glow_rrect(p, QRectF(-w / 2, -h / 2, w, h), 4)
    p.restore()


# ══════════════════════════════════════════════════════════════════════
# NEW EMOTIONS
# ══════════════════════════════════════════════════════════════════════

def draw_nervous(p: QPainter) -> None:
    """One eye notably larger than the other — anxious, uneven vigilance."""
    _glow_rrect(p, QRectF(LEFT_CX - 12, EYE_CY - 16, 24, 32), 5)   # tall/wide
    _glow_rrect(p, QRectF(RIGHT_CX - 9, EYE_CY - 9, 18, 18), 4)    # smaller


def draw_dizzy(p: QPainter) -> None:
    """Spiral eyes — completely overwhelmed, head spinning."""
    for cx in (LEFT_CX, RIGHT_CX):
        _glow_stroke(p, _make_spiral(cx, EYE_CY, turns=2, max_r=15.0), thick=2.5)


def draw_smug(p: QPainter) -> None:
    """One squinted slit, one half-open — asymmetric self-satisfaction."""
    _glow_rrect(p, QRectF(LEFT_CX - 15, EYE_CY - 4, 30, 8), 3)    # near-closed
    _glow_rrect(p, QRectF(RIGHT_CX - 15, EYE_CY - 14, 30, 28), 5)  # open


def draw_worried(p: QPainter) -> None:
    """Both squares tilted top-inward — classic anxiety furrow."""
    w, h = 28, 24
    p.save()
    p.translate(LEFT_CX, EYE_CY)
    p.rotate(10)
    _glow_rrect(p, QRectF(-w / 2, -h / 2, w, h), 4)
    p.restore()
    p.save()
    p.translate(RIGHT_CX, EYE_CY)
    p.rotate(-10)
    _glow_rrect(p, QRectF(-w / 2, -h / 2, w, h), 4)
    p.restore()


def draw_proud(p: QPainter) -> None:
    """Tall upright rectangles — standing at full height."""
    w, h = 24, 36
    _glow_rrect(p, QRectF(LEFT_CX - w / 2, EYE_CY - h / 2, w, h), 5)
    _glow_rrect(p, QRectF(RIGHT_CX - w / 2, EYE_CY - h / 2, w, h), 5)


def draw_disgusted(p: QPainter) -> None:
    """One eye wide open, other angled almost shut — one-sided grimace."""
    _glow_rrect(p, QRectF(LEFT_CX - 15, EYE_CY - 14, 30, 28), 5)  # normal
    p.save()
    p.translate(RIGHT_CX, EYE_CY + 2)
    p.rotate(8)
    _glow_rrect(p, QRectF(-15, -4, 30, 7), 3)                       # tilted squint
    p.restore()


def draw_overwhelmed(p: QPainter) -> None:
    """Huge wide-open eyes + tears — too much input at once."""
    w, h = 40, 40
    _glow_rrect(p, QRectF(LEFT_CX - w / 2, EYE_CY - h / 2, w, h), 11)
    _glow_rrect(p, QRectF(RIGHT_CX - w / 2, EYE_CY - h / 2, w, h), 11)
    _glow_path(p, _make_teardrop(LEFT_CX, EYE_CY + 24, 6))
    _glow_path(p, _make_teardrop(RIGHT_CX, EYE_CY + 24, 6))


def draw_determined(p: QPainter) -> None:
    """Strong narrow squint, both eyes slightly angled inward — steely resolve."""
    w, h = 32, 10
    p.save()
    p.translate(LEFT_CX, EYE_CY)
    p.rotate(5)
    _glow_rrect(p, QRectF(-w / 2, -h / 2, w, h), 3)
    p.restore()
    p.save()
    p.translate(RIGHT_CX, EYE_CY)
    p.rotate(-5)
    _glow_rrect(p, QRectF(-w / 2, -h / 2, w, h), 3)
    p.restore()


def draw_mischievous(p: QPainter) -> None:
    """One raised tilted square, one squint — scheming asymmetry."""
    p.save()
    p.translate(LEFT_CX, EYE_CY - 4)   # raised slightly
    p.rotate(-10)
    _glow_rrect(p, QRectF(-14, -14, 28, 28), 5)
    p.restore()
    _glow_rrect(p, QRectF(RIGHT_CX - 15, EYE_CY - 5, 30, 10), 4)   # squint


def draw_in_love(p: QPainter) -> None:
    """Dreamy half-closed eyes — soft, floaty, lovesick."""
    aw, ah = 32, 26
    # Half-open: draw only bottom half of arc (closed lids drooping)
    _glow_arc(p, QRectF(LEFT_CX - aw / 2, EYE_CY - ah / 2, aw, ah), 0, 180, 10)
    _glow_arc(p, QRectF(RIGHT_CX - aw / 2, EYE_CY - ah / 2, aw, ah), 0, 180, 10)
    # Tiny heart floating above each eye
    _glow_path(p, _make_heart(LEFT_CX, EYE_CY - 26, 7))
    _glow_path(p, _make_heart(RIGHT_CX, EYE_CY - 26, 7))


def draw_electric(p: QPainter) -> None:
    """Lightning bolt eyes — charged up, high voltage energy."""
    _glow_path(p, _make_lightning(LEFT_CX, EYE_CY, 16))
    _glow_path(p, _make_lightning(RIGHT_CX, EYE_CY, 16))


def draw_pleading(p: QPainter) -> None:
    """Large, slightly upward-tilted circles + teardrop — puppy eyes."""
    _glow_ring(p, LEFT_CX, EYE_CY - 2, 17, 6)
    _glow_ring(p, RIGHT_CX, EYE_CY - 2, 17, 6)
    # Quivering single teardrop on one side
    _glow_path(p, _make_teardrop(LEFT_CX - 4, EYE_CY + 20, 6))


def draw_suspicious(p: QPainter) -> None:
    """Both eyes very narrow with a slight inward tilt — narrowed gaze."""
    w, h = 28, 8
    p.save()
    p.translate(LEFT_CX, EYE_CY)
    p.rotate(6)
    _glow_rrect(p, QRectF(-w / 2, -h / 2, w, h), 3)
    p.restore()
    p.save()
    p.translate(RIGHT_CX, EYE_CY)
    p.rotate(-6)
    _glow_rrect(p, QRectF(-w / 2, -h / 2, w, h), 3)
    p.restore()


def draw_awestruck(p: QPainter) -> None:
    """Very large open rings + highlight dots — jaw-dropped wonder."""
    _glow_ring(p, LEFT_CX, EYE_CY, 20, 10)
    _glow_ring(p, RIGHT_CX, EYE_CY, 20, 10)
    _glow_dot(p, LEFT_CX + 6, EYE_CY - 6, 3)
    _glow_dot(p, RIGHT_CX + 6, EYE_CY - 6, 3)


def draw_tired(p: QPainter) -> None:
    """Half-open eyes drooping down — fighting to stay awake."""
    aw, ah = 30, 28
    # Top arc only — like a heavy lid dragging down
    _glow_arc(p, QRectF(LEFT_CX - aw / 2, EYE_CY - ah / 2, aw, ah), 0, 180, 9)
    _glow_arc(p, QRectF(RIGHT_CX - aw / 2, EYE_CY - ah / 2, aw, ah), 0, 180, 9)
    # Drooping lid bars just below center
    _glow_rrect(p, QRectF(LEFT_CX - 14, EYE_CY + 2, 28, 7), 3)
    _glow_rrect(p, QRectF(RIGHT_CX - 14, EYE_CY + 2, 28, 7), 3)

def draw_empathic(p: QPainter) -> None:
    """Soft, slightly outward-tilted eyes — conveying warm, gentle listening."""
    w, h = 28, 22
    p.save()
    p.translate(LEFT_CX, EYE_CY + 2)
    p.rotate(-8)  # Tilt outward slightly for a soft, open look
    _glow_rrect(p, QRectF(-w / 2, -h / 2, w, h), 6) # Softer border radius
    p.restore()
    
    p.save()
    p.translate(RIGHT_CX, EYE_CY + 2)
    p.rotate(8)
    _glow_rrect(p, QRectF(-w / 2, -h / 2, w, h), 6)
    p.restore()
    
# ──────────────────────────────────────────────────────────────
# ALIASES — Mapping new tags to closest existing visuals
# ──────────────────────────────────────────────────────────────
draw_tease = draw_playful
draw_gentle = draw_empathic
draw_supportive = draw_empathic