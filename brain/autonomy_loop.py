"""
brain/autonomy_loop.py — The Curiosity Engine
════════════════════════════════════════════
Monitors B's conversation activity and user's context.
"""

import logging
import random
import threading
import time

logger = logging.getLogger("B.brain.autonomy")

class AutonomyEngine:
    def __init__(self, bus):
        self._bus = bus
        self._latest_context = None
        self._last_activity_time = time.monotonic()
        self._is_busy = False
        self._work_mode = False
        self._behavior_mode = "wander"
        self._running = True
        
        # Subscribe to activity events
        self._bus.subscribe("b_finished_speaking", self._on_activity)
        self._bus.subscribe("user_spoke", self._on_activity)
        self._bus.subscribe("context_updated", self._on_context_updated)
        self._bus.subscribe("user_activity_updated", self._on_user_activity_updated)
        self._bus.subscribe("b_set_behavior", self._on_set_behavior)
        self._bus.subscribe("b_work_mode_toggled", self._on_work_mode_toggled)

        # Determine the initial random interval
        self._reset_timer()
        self._thread = threading.Thread(
            target=self._curiosity_loop,
            daemon=True,
            name="CuriosityWorker"
        )
        self._thread.start()
        logger.info("AutonomyEngine (Loop) initialized")

    def _on_user_activity_updated(self, payload: dict):
        level = payload.get("level", "low")
        if level == "high":
            if not self._is_busy:
                self._is_busy = True
                logger.info("User is focused. B will be more subtle.")
                self._bus.publish("emotion_changed", {"emotion": "focused", "intensity": 0.6})
                self._last_activity_time = time.monotonic()
        else:
            if self._is_busy:
                self._is_busy = False
                if self._behavior_mode != "corner":
                    self._bus.publish("emotion_changed", {"emotion": "neutral", "intensity": 1.0})

    def _on_work_mode_toggled(self, payload: dict):
        self._work_mode = payload.get("active", False)
        if self._work_mode:
            self._target_interval = random.uniform(15, 30) 
        else:
            self._reset_timer()
        logger.info(f"Autonomy adjusted for Work Mode: {self._work_mode} (Interval: {self._target_interval:.1f}s)")

    def _on_set_behavior(self, payload: dict):
        self._behavior_mode = payload.get("mode", "wander")
        if self._behavior_mode == "follow":
            self._target_interval = random.uniform(60, 180)
        elif self._behavior_mode == "wander":
            self._target_interval = random.uniform(180, 420)
        self._reset_timer()

    def _reset_timer(self):
        self._last_activity_time = time.monotonic()
        if self._work_mode:
            # Keep the work mode pace even after activity
            self._target_interval = random.uniform(15, 30)
        else:
            self._target_interval = random.randint(3 * 60, 7 * 60)

    def _on_activity(self, payload: dict):
        self._reset_timer()

    def _on_context_updated(self, payload: dict):
        self._latest_context = payload

    def _curiosity_loop(self):
        while self._running:
            time.sleep(5.0)
            
            if self._behavior_mode == "corner":
                continue

            # In Work Mode, we ignore 'busy' unless B is already speaking
            if self._is_busy and not self._work_mode:
                continue

            elapsed = time.monotonic() - self._last_activity_time
            if elapsed > self._target_interval:
                if self._latest_context:
                    logger.info("Triggering proactive thought (Work Mode: %s)", self._work_mode)
                    self._bus.publish("trigger_proactive_thought", {
                        "context": self._latest_context,
                        "mode": "work" if self._work_mode else self._behavior_mode
                    })
                    self._reset_timer()
                else:
                    pass

    def stop(self):
        self._running = False
