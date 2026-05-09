"""
physics/kinematics.py — The Antigravity Engine
═══════════════════════════════════════════════

This module is the reason B feels alive. It computes 2D position updates
using spring-based physics: Hooke's Law for attraction toward targets,
velocity damping for smoothness, and edge repulsion for containment.

B doesn't teleport. He doesn't snap. He drifts, floats, and glides
like a creature with zero gravity — hence "antigravity."

Physics Model:
    Every tick (dt ≈ 16ms at 60fps):

    1. WANDER: Pick a random target point on screen every 4-8 seconds.
    2. SPRING: Apply Hooke's Law toward the target:
         F_spring = -k * (position - target)
    3. EDGE REPULSION: If within margin of screen edge, apply outward force:
         F_repulse = repulse_k * (margin - distance_to_edge) * direction
    4. DAMPING: Apply velocity damping to prevent oscillation:
         F_damping = -c * velocity
    5. INTEGRATE: Euler integration (good enough for visual motion):
         velocity += (F_net / mass) * dt
         position += velocity * dt

Tuning Philosophy:
    - SPRING_K is very low (0.02) → B drifts slowly, never snaps
    - DAMPING_C is high (0.92) → movement is smooth, not bouncy
    - MAX_VELOCITY caps speed → prevents wild acceleration
    - Wander interval is randomized → feels organic, not robotic
"""

from __future__ import annotations

import logging
import random
import time
import math
import ctypes
from ctypes import wintypes
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PyQt6.QtCore import QRect
    from core.bus import EventBus

logger = logging.getLogger("B.physics.kinematics")

# ──────────────────────────────────────────────────────────────────────
# Physics constants — these define B's personality in motion.
# Every value here was chosen to make B feel dreamy and weightless.
# ──────────────────────────────────────────────────────────────────────

SPRING_K         = 0.02     # Spring stiffness (very soft — drifty)
DAMPING_C        = 0.92     # Velocity damping per tick (0.92 = heavy smoothing)
MASS             = 1.0      # Unit mass (simplifies force→acceleration)
EDGE_REPULSE_K   = 0.05     # Edge repulsion spring stiffness
EDGE_MARGIN      = 80       # px — how close to edge before repulsion kicks in
MAX_VELOCITY     = 12.0     # px/tick — speed cap to prevent wild movement
WANDER_MIN_SEC   = 4.0      # Minimum seconds between wander target changes
WANDER_MAX_SEC   = 8.0      # Maximum seconds between wander target changes
FACE_W           = 180      # px — must match ui/overlay.py CANVAS_W
FACE_H           = 112      # px — must match ui/overlay.py CANVAS_H
SCREEN_PADDING   = 20       # px — minimum distance from absolute screen edge
FOLLOW_RADIUS    = 220      # px — B stays on the edge of this circle in Follow mode
WANDER_REST_SEC  = 15.0     # px — minimum time B stays at a deliberate target
WANDER_CHANCE_CORNER = 0.6  # 60% chance to return to a corner instead of a UI element


class KinematicsEngine:
    """
    Computes B's position using spring physics.

    This engine subscribes to "tick" events and publishes
    "position_updated" events. It has NO knowledge of Qt widgets,
    windows, or rendering. It only knows math and coordinates.

    The engine maintains:
        - position (x, y): current position in screen coordinates
        - velocity (vx, vy): current velocity vector
        - target (tx, ty): the point B is drifting toward
        - wander_timer: countdown until next random target
    """

    def __init__(self, bus: EventBus, screen_rect: QRect) -> None:
        self._bus = bus

        # ──────────────────────────────────────────────────────────────
        # Screen geometry — injected from main.py, NOT queried here.
        # This keeps the physics engine decoupled from Qt's display
        # system and ensures correct behavior on multi-monitor setups.
        #
        # screen_rect is a QRect from primaryScreen().availableGeometry().
        # It includes the offset (x, y) for multi-monitor configurations
        # where the primary screen may not start at (0, 0).
        # ──────────────────────────────────────────────────────────────
        self._screen_x = screen_rect.x()       # Screen origin X offset
        self._screen_y = screen_rect.y()       # Screen origin Y offset
        self._screen_w = screen_rect.width()
        self._screen_h = screen_rect.height()

        # ──────────────────────────────────────────────────────────────
        # Initial position: top-right corner
        #
        # Starting center-screen is jarring — it blocks whatever app
        # is currently focused. Top-right is unobtrusive: visible in
        # peripheral vision but out of the way of code editors and
        # browser windows that typically anchor top-left.
        # ──────────────────────────────────────────────────────────────
        self._x = float(self._screen_x + self._screen_w - 200)
        self._y = float(self._screen_y + 100)

        # ──────────────────────────────────────────────────────────────
        # Velocity vector — starts at rest
        # ──────────────────────────────────────────────────────────────
        self._vx = 0.0
        self._vy = 0.0

        # ──────────────────────────────────────────────────────────────
        # Initial wander target — start at center, will pick a new
        # one after the first wander interval expires.
        # ──────────────────────────────────────────────────────────────
        self._target_x = self._x
        self._target_y = self._y

        # ──────────────────────────────────────────────────────────────
        # Wander timer — tracks when to pick a new random target.
        # We use wall-clock time (not tick counting) for robustness
        # against frame drops.
        # ──────────────────────────────────────────────────────────────
        self._last_wander_time = time.monotonic()
        self._wander_interval = self._random_wander_interval()

        # ──────────────────────────────────────────────────────────────
        # Behavioral state
        self._mode = "wander"
        self._hover_start_time = 0.0
        self._last_is_over = False
        self._hover_short_threshold = 0.3 # 300ms for follow/wander toggle
        self._hover_long_threshold = 1.2  # 1.2s for corner/behave mode
        
        # Subscribe to behavior changes
        self._bus.subscribe("b_set_behavior", self._on_set_behavior)
        self._bus.subscribe("b_move_request", self._on_move_request)
        self._bus.subscribe("context_updated", self._on_context_updated)
        
        # Spatial Awareness for deliberate wandering
        self._last_spatial_map = {}
        
        # Pointing state
        self._point_start_time = 0.0
        
        # Subscribe to the tick event — this drives the physics loop
        # ──────────────────────────────────────────────────────────────
        self._bus.subscribe("tick", self._on_tick, priority=10)

        logger.info(
            "KinematicsEngine initialized (screen: %dx%d, start: %.0f,%.0f)",
            self._screen_w,
            self._screen_h,
            self._x,
            self._y,
        )

    def _on_tick(self, payload: dict) -> None:
        """
        Physics update — called every frame (~60fps).

        This is the hot path. Every line here runs 60 times per second.
        We avoid object allocations, function calls, and anything that
        would trigger Python's GC in this loop.

        Args:
            payload: {"dt": float} — time since last tick in seconds
        """
        dt = payload.get("dt", 0.016)

        # ──────────────────────────────────────────────────────────────
        # Step 1: WANDER — periodically pick a new random target
        # ──────────────────────────────────────────────────────────────
        now = time.monotonic()
        if self._mode == "wander":
            if now - self._last_wander_time >= self._wander_interval:
                self._pick_new_wander_target()
                self._last_wander_time = now
                self._wander_interval = self._random_wander_interval()
        elif self._mode == "point":
            # Stay at the point for 6 seconds, then go back to wandering
            if now - self._point_start_time > 6.0:
                logger.info("Pointing duration expired. Returning to wander mode.")
                self._mode = "wander"
                self._last_wander_time = now
        elif self._mode == "corner":
            # Target the top-right corner safe zone
            self._target_x = float(self._screen_x + self._screen_w - FACE_W - SCREEN_PADDING - 50)
            self._target_y = float(self._screen_y + SCREEN_PADDING + 50)
        elif self._mode == "watch":
            # Target the center-right "watching" area
            self._target_x = float(self._screen_x + self._screen_w * 0.75)
            self._target_y = float(self._screen_y + self._screen_h * 0.5)
        elif self._mode == "follow":
            # Target a point on a radius around the mouse position
            try:
                pt = wintypes.POINT()
                ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
                mx, my = float(pt.x), float(pt.y)
                
                # Calculate vector from cursor to B's current position
                dx = self._x - mx
                dy = self._y - my
                dist = math.sqrt(dx*dx + dy*dy)
                
                if dist > 0:
                    # Target is the point on the circle closest to B
                    self._target_x = mx + (dx / dist) * FOLLOW_RADIUS
                    self._target_y = my + (dy / dist) * FOLLOW_RADIUS
                else:
                    # Fallback if B is exactly on the cursor
                    self._target_x = mx + FOLLOW_RADIUS
                    self._target_y = my
            except Exception:
                pass

        # ──────────────────────────────────────────────────────────────
        # Step 2: SPRING FORCE — Hooke's Law pulling toward target
        #
        # F_spring = -k * displacement
        # ──────────────────────────────────────────────────────────────
        dx = self._x - self._target_x
        dy = self._y - self._target_y
        dist = math.sqrt(dx*dx + dy*dy)

        # DYNAMIC STIFFNESS: Snappy when far, calm when near
        k = SPRING_K
        damping = DAMPING_C
        
        if self._mode == "follow":
            # Scale k from 0.04 (near) to 0.12 (far)
            # This makes B 'rush' to catch up but glide in smoothly.
            k_factor = min(1.0, dist / 800.0)
            k = 0.04 + (k_factor * 0.08)
            
            # Adjust damping: heavier damping when near to prevent overshoot
            if dist < 150:
                damping = 0.88 # More resistance
            else:
                damping = 0.94 # Less resistance (more slide)
        elif self._mode == "point":
            # Snappy, high-speed movement for pointing
            k = 0.18
            damping = 0.82

        fx = -k * dx
        fy = -k * dy

        # ──────────────────────────────────────────────────────────────
        # Step 3: EDGE REPULSION — soft bounce away from screen edges
        # ──────────────────────────────────────────────────────────────
        fx += self._edge_repulsion_x()
        fy += self._edge_repulsion_y()

        # ──────────────────────────────────────────────────────────────
        # Step 4: DAMPING — bleed off velocity to prevent oscillation
        # ──────────────────────────────────────────────────────────────
        self._vx = (self._vx + fx * dt) * damping
        self._vy = (self._vy + fy * dt) * damping

        # ──────────────────────────────────────────────────────────────
        # Step 5: VELOCITY CAP — prevent wild acceleration
        #
        # If external forces (edge repulsion, rapid target changes)
        # cause velocity to spike, clamp it. This prevents B from
        # ever appearing to "teleport" across the screen.
        # ──────────────────────────────────────────────────────────────
        speed_sq = self._vx * self._vx + self._vy * self._vy
        max_sq = MAX_VELOCITY * MAX_VELOCITY
        if speed_sq > max_sq:
            # Scale velocity vector down to MAX_VELOCITY magnitude
            # Using fast inverse sqrt approximation would be overkill
            # here — math.sqrt once per frame is fine.
            scale = MAX_VELOCITY / (speed_sq ** 0.5)
            self._vx *= scale
            self._vy *= scale

        # ──────────────────────────────────────────────────────────────
        # Step 6: INTEGRATE — update position
        #
        # Simple Euler integration: pos += vel * dt
        # Euler is good enough for visual animation. We're not landing
        # a rocket — we're drifting a pixel soul.
        # ──────────────────────────────────────────────────────────────
        self._x += self._vx
        self._y += self._vy

        # ──────────────────────────────────────────────────────────────
        # Step 7: HARD CLAMP — absolute safety net
        #
        # Even with edge repulsion, floating-point drift could push B
        # slightly off screen. This hard clamp is a safety net — it
        # should rarely activate. If it does, we also zero the velocity
        # component to prevent "sticking" to the edge.
        # ──────────────────────────────────────────────────────────────
        min_x = float(self._screen_x + SCREEN_PADDING)
        max_x = float(self._screen_x + self._screen_w - FACE_W - SCREEN_PADDING)
        min_y = float(self._screen_y + SCREEN_PADDING)
        max_y = float(self._screen_y + self._screen_h - FACE_H - SCREEN_PADDING)

        if self._x < min_x:
            self._x = min_x
            self._vx = abs(self._vx) * 0.3  # Gentle bounce
        elif self._x > max_x:
            self._x = max_x
            self._vx = -abs(self._vx) * 0.3

        if self._y < min_y:
            self._y = min_y
            self._vy = abs(self._vy) * 0.3
        elif self._y > max_y:
            self._y = max_y
            self._vy = -abs(self._vy) * 0.3

        # ──────────────────────────────────────────────────────────────
        # Step 7.5: MOUSE HOVER DETECTION (Dwell-based Toggle)
        # ──────────────────────────────────────────────────────────────
        self._check_mouse_hover()

        # ──────────────────────────────────────────────────────────────
        # Step 8: PUBLISH — broadcast the new position
        #
        # The WindowManager is subscribed to this event and will move
        # the overlay window accordingly. The KinematicsEngine has no
        # idea who's listening — it just publishes coordinates.
        # ──────────────────────────────────────────────────────────────
        self._bus.publish("position_updated", {"x": self._x, "y": self._y})

    def _edge_repulsion_x(self) -> float:
        """
        Calculate horizontal edge repulsion force.

        Returns a force value:
            - Positive force when near the left edge (pushes right)
            - Negative force when near the right edge (pushes left)
            - Zero when comfortably within the screen interior
        """
        force = 0.0
        max_x = self._screen_x + self._screen_w - FACE_W - SCREEN_PADDING

        # Near left edge?
        left_dist = self._x - (self._screen_x + SCREEN_PADDING)
        if left_dist < EDGE_MARGIN:
            # Force is proportional to penetration depth into margin
            penetration = EDGE_MARGIN - left_dist
            force += EDGE_REPULSE_K * penetration

        # Near right edge?
        right_dist = max_x - self._x
        if right_dist < EDGE_MARGIN:
            penetration = EDGE_MARGIN - right_dist
            force -= EDGE_REPULSE_K * penetration

        return force

    def _edge_repulsion_y(self) -> float:
        """
        Calculate vertical edge repulsion force.

        Same logic as _edge_repulsion_x but for top/bottom edges.
        """
        force = 0.0
        max_y = self._screen_y + self._screen_h - FACE_H - SCREEN_PADDING

        # Near top edge?
        top_dist = self._y - (self._screen_y + SCREEN_PADDING)
        if top_dist < EDGE_MARGIN:
            penetration = EDGE_MARGIN - top_dist
            force += EDGE_REPULSE_K * penetration

        # Near bottom edge?
        bottom_dist = max_y - self._y
        if bottom_dist < EDGE_MARGIN:
            penetration = EDGE_MARGIN - bottom_dist
            force -= EDGE_REPULSE_K * penetration

        return force

    def _pick_new_wander_target(self) -> None:
        """
        Select a new point for B to drift toward. 
        Instead of pure randomness, we now prioritize:
        1. Screen corners (resting positions)
        2. Known UI elements (deliberate inspection)
        """
        now = time.monotonic()
        
        # 60% chance to go to a resting corner, or if no UI elements are known
        if random.random() < WANDER_CHANCE_CORNER or not self._last_spatial_map:
            # Pick one of the four corners (with padding)
            corners = [
                (self._screen_x + SCREEN_PADDING + 50, self._screen_y + SCREEN_PADDING + 50), # Top Left
                (self._screen_x + self._screen_w - FACE_W - 50, self._screen_y + SCREEN_PADDING + 50), # Top Right
                (self._screen_x + self._screen_w - FACE_W - 50, self._screen_y + self._screen_h - FACE_H - 100), # Bottom Right
                (self._screen_x + SCREEN_PADDING + 50, self._screen_y + self._screen_h - FACE_H - 100), # Bottom Left
            ]
            self._target_x, self._target_y = random.choice(corners)
            logger.debug("Deliberate Wander: Resting in corner at (%.0f, %.0f)", self._target_x, self._target_y)
        else:
            # Pick a random UI element to "inspect"
            target_id = random.choice(list(self._last_spatial_map.keys()))
            data = self._last_spatial_map[target_id]
            
            # Use the dict format we standardized earlier
            if isinstance(data, dict):
                tx, ty = data["coords"]
            else:
                tx, ty = data[0], data[1] # Fallback for legacy
            
            # Float NEAR the element, don't cover it
            self._target_x = float(tx + 40)
            self._target_y = float(ty - FACE_H - 20)
            logger.debug("Deliberate Wander: Inspecting element [#%s] at (%.0f, %.0f)", target_id, self._target_x, self._target_y)

        self._last_wander_time = now
        # Slow down the wandering! Rest for 12-25 seconds at each spot.
        self._wander_interval = random.uniform(12.0, 25.0)

    def _on_context_updated(self, payload: dict) -> None:
        """Keep track of screen elements for deliberate movement."""
        new_map = payload.get("spatial_map", {})
        if new_map:
            self._last_spatial_map = new_map

    def _on_move_request(self, payload: dict) -> None:
        """Handle a direct coordinate movement request (usually from LLM)."""
        tx = payload.get("x")
        ty = payload.get("y")
        
        if tx is not None and ty is not None:
            # Offset the target so B's center isn't directly on top of the text
            # Offset the target so B's bottom-left corner points to the text
            # We want B to "point" at it, usually by being slightly to the right or above
            self._target_x = float(tx + 10)
            self._target_y = float(ty - FACE_H - 10)
            
            logger.info("Kinematics: Move Request Received! Target Center: (%.0f, %.0f) -> Offset Destination: (%.0f, %.0f)", tx, ty, self._target_x, self._target_y)
            self._mode = "point"
            self._point_start_time = time.monotonic()

    def _on_set_behavior(self, payload: dict) -> None:
        """Change B's movement behavior mode."""
        new_mode = payload.get("mode", "wander")
        if new_mode in ["wander", "corner", "watch", "follow", "point"]:
            if self._mode != new_mode:
                logger.info(f"Kinematics behavior changed: {self._mode} -> {new_mode}")
                self._mode = new_mode
                if new_mode == "wander":
                    self._last_wander_time = 0 # Force immediate target pick
                elif new_mode == "point":
                    self._point_start_time = time.monotonic()

    def _check_mouse_hover(self) -> None:
        """Polls global mouse position and detects duration-based state changes."""
        try:
            pt = wintypes.POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
            mx, my = pt.x, pt.y
            
            is_over = (self._x <= mx <= self._x + FACE_W) and (self._y <= my <= self._y + FACE_H)
            
            now = time.monotonic()
            
            if is_over:
                if not self._last_is_over:
                    # Mouse just entered
                    self._hover_start_time = now
                else:
                    # Mouse is dwelling. Check for LONG hover (2s)
                    if self._hover_start_time > 0 and (now - self._hover_start_time >= self._hover_long_threshold):
                        if self._mode != "corner":
                            logger.info("Long hover detected! Dismissing to corner.")
                            self._bus.publish("b_set_behavior", {"mode": "corner"})
                        # Clear start time so we don't re-trigger
                        self._hover_start_time = 0.0
            else:
                if self._last_is_over:
                    # Mouse just LEFT. Check for SHORT hover (0.3s - 1.9s)
                    dwell_duration = now - self._hover_start_time
                    if self._hover_short_threshold <= dwell_duration < self._hover_long_threshold:
                        # Toggle logic: Corner -> Follow -> Wander -> Follow
                        if self._mode == "corner":
                            new_mode = "follow"
                        elif self._mode == "follow":
                            new_mode = "wander"
                        else:
                            new_mode = "follow"
                        
                        logger.info(f"Short hover detected ({dwell_duration:.2f}s)! Switching to {new_mode}")
                        self._bus.publish("b_set_behavior", {"mode": new_mode})
                    
                    self._hover_start_time = 0.0
                
            self._last_is_over = is_over
            
        except Exception:
            pass

    @staticmethod
    def _random_wander_interval() -> float:
        """Return a random interval between WANDER_MIN_SEC and WANDER_MAX_SEC."""
        return random.uniform(WANDER_MIN_SEC, WANDER_MAX_SEC)
