"""
brain/autonomy.py — The Curiosity Engine
════════════════════════════════════════
Monitors B's conversation activity and user's context.
If B hasn't spoken in a while and there is new context,
B will spontaneously initiate a conversation.
"""

from __future__ import annotations

import logging
import random
import threading
import time

logger = logging.getLogger("B.brain.autonomy")

class AutonomyEngine:
    def __init__(self, bus):
        self._bus = bus
        self._latest_context = None
        self._last_activity_time = time.time()
        self._target_interval = 0
        
        # Determine the initial random interval
        self._reset_timer()
        
        # Subscribe to activity events
        self._bus.subscribe("b_finished_speaking", self._on_activity)
        self._bus.subscribe("user_spoke", self._on_activity)
        self._bus.subscribe("context_updated", self._on_context_updated)
        self._bus.subscribe("user_activity_updated", self._on_user_activity_updated)
        self._bus.subscribe("b_set_behavior", self._on_set_behavior)
        
        self._is_busy = False
        self._behavior_mode = "wander"
        self._running = True
        self._thread = threading.Thread(
            target=self._curiosity_loop,
            daemon=True,
            name="CuriosityWorker"
        )
        self._thread.start()
        logger.info("AutonomyEngine initialized")

    def _on_user_activity_updated(self, payload: dict):
        level = payload.get("level", "low")
        if level == "high":
            if not self._is_busy:
                self._is_busy = True
                logger.info("User is focused. B will be more subtle but stays where he is.")
                self._bus.publish("emotion_changed", {"emotion": "focused", "intensity": 0.6})
                # Reset activity timer to postpone curiosity slightly
                self._last_activity_time = time.time()
        else:
            if self._is_busy:
                self._is_busy = False
                logger.info("User activity lowered.")
                if self._behavior_mode != "corner":
                    self._bus.publish("emotion_changed", {"emotion": "neutral", "intensity": 1.0})

    def _on_set_behavior(self, payload: dict):
        self._behavior_mode = payload.get("mode", "wander")
        
        # Adjust proactivity interval based on mode
        if self._behavior_mode == "follow":
            self._target_interval = random.uniform(60, 180) # 1-3 mins (Engaged)
        elif self._behavior_mode == "wander":
            self._target_interval = random.uniform(180, 420) # 3-7 mins (Relaxed)
            
        logger.info(f"Autonomy proactivity adjusted for {self._behavior_mode} mode")
        self._reset_timer()

    def _reset_timer(self):
        """Reset the timer and pick a new random interval between 3 and 7 minutes."""
        self._last_activity_time = time.time()
        # 3 to 7 minutes in seconds
        self._target_interval = random.randint(3 * 60, 7 * 60)
        logger.debug(f"Autonomy timer reset. Next proactive thought in {self._target_interval}s")

    def _on_activity(self, payload: dict):
        """Any speech activity resets the proactive timer."""
        self._reset_timer()

    def _on_context_updated(self, payload: dict):
        """Store the latest context."""
        self._latest_context = payload

    def _curiosity_loop(self):
        """Background loop that occasionally triggers proactive thoughts."""
        while self._running:
            time.sleep(5.0)
            
            # SUSPEND PROACTIVITY IF DISMISSED TO CORNER
            if self._behavior_mode == "corner":
                continue

            # Skip if B is already talking or user is active
            if self._is_busy and self._behavior_mode != "follow":
                continue

            elapsed = time.monotonic() - self._last_activity_time
            if elapsed > self._target_interval:
                if self._latest_context:
                    # We've waited long enough and we have something to look at!
                    logger.info("Triggering proactive thought (Mode: %s)", self._behavior_mode)
                    self._bus.publish("trigger_proactive_thought", {
                        "context": self._latest_context,
                        "mode": self._behavior_mode
                    })
                    self._reset_timer()
                    # Consume the context
                    self._latest_context = None
                else:
                    # No context yet, just keep waiting until we see something new
                    pass

    def stop(self):
        self._running = False
