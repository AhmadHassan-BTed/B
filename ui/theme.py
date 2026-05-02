"""
ui/theme.py — B's Visual Identity
══════════════════════════════════

All visual constants in one place: colors, dimensions, layout.
Imported by face.py and expressions.py. No logic, just values.
"""

from PyQt6.QtGui import QColor

# ──────────────────────────────────────────────────────────────────────
# Canvas — the visor fills the entire window, no border, no padding.
# ──────────────────────────────────────────────────────────────────────
CANVAS_W = 180
CANVAS_H = 112
VISOR_RADIUS = 18

# ──────────────────────────────────────────────────────────────────────
# Eye positions (center coordinates on the 180×112 canvas)
# ──────────────────────────────────────────────────────────────────────
LEFT_CX = 54
RIGHT_CX = 126
EYE_CY = 54

# ──────────────────────────────────────────────────────────────────────
# Glow spread (pixels of bloom around shapes)
# ──────────────────────────────────────────────────────────────────────
GLOW = 5

# ──────────────────────────────────────────────────────────────────────
# Colors — bright cyan glow on pure black visor
# ──────────────────────────────────────────────────────────────────────
CYAN     = QColor(0, 230, 255)           # Core eye color
VISOR_BG = QColor(0, 0, 0)              # Pure black — no border artifacts
CLEAR    = QColor(0, 0, 0, 0)           # Fully transparent

def GET_PALETTE(emotion: str) -> tuple[QColor, QColor, QColor]:
    """Returns (core, mid, dim) colors for the given emotion."""
    base = CYAN
    
    if emotion == "angry":
        base = QColor(255, 50, 50)
    elif emotion == "confused":
        base = QColor(255, 230, 0)
    elif emotion in ["love_struck", "in_love"]:
        base = QColor(255, 100, 200)
    elif emotion == "disgusted":
        base = QColor(30, 180, 50)
    
    # Generate mid and dim variants with alpha
    core = QColor(base)
    mid = QColor(base.red(), base.green(), base.blue(), 130)
    dim = QColor(base.red(), base.green(), base.blue(), 50)
    
    return core, mid, dim

# ──────────────────────────────────────────────────────────────────────
# Transition timing
# ──────────────────────────────────────────────────────────────────────
TRANSITION_SECS = 0.42   # Slightly snappier morph for more responsive emotional feedback
