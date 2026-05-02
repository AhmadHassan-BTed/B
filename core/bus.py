"""
core/bus.py — The Central Nervous System of B
═══════════════════════════════════════════════

The EventBus is the ONLY way modules communicate. No module ever imports
another module. They publish events and subscribe to events. Period.

Architecture:
    - Synchronous dispatch (no threads, no asyncio overhead)
    - Priority-based subscriber ordering (lower number = higher priority)
    - Wildcard subscription via "*" for debug/logging hooks
    - Type-safe event payloads via plain dicts (no dataclass overhead)

Usage:
    bus = EventBus()
    bus.subscribe("position_updated", my_callback, priority=0)
    bus.publish("position_updated", {"x": 100.0, "y": 200.0})
"""

from __future__ import annotations

import logging
from typing import Any, Callable

# ──────────────────────────────────────────────────────────────────────
# Module-level logger. Each module gets its own logger under the "B"
# namespace so we can filter logs per-subsystem.
# ──────────────────────────────────────────────────────────────────────
logger = logging.getLogger("B.core.bus")


class EventBus:
    """
    A lightweight, synchronous publish/subscribe event bus.

    Design constraints:
        1. ZERO external dependencies — pure Python stdlib.
        2. Synchronous dispatch — events are delivered inline on the
           caller's thread. No queues, no locks, no context switches.
           This is intentional: the entire app runs on Qt's event loop
           thread, so synchronous dispatch gives us deterministic
           ordering with zero overhead.
        3. Priority ordering — subscribers with lower priority numbers
           are called first. Default priority is 100. The WindowManager
           uses priority 0 so it processes position updates before
           anything else (minimizes visual latency).
        4. Wildcard "*" — subscribing to "*" receives ALL events. Used
           for debugging and future telemetry.
    """

    def __init__(self) -> None:
        # ──────────────────────────────────────────────────────────────
        # Internal storage: event_name -> list of (priority, callback)
        # Sorted by priority on each subscribe call. We sort once at
        # subscribe time, not at dispatch time — optimizing for the
        # hot path (publish is called 60x/sec, subscribe is called once).
        # ──────────────────────────────────────────────────────────────
        self._subscribers: dict[str, list[tuple[int, Callable]]] = {}
        logger.debug("EventBus initialized")

    def subscribe(
        self,
        event_name: str,
        callback: Callable[[dict[str, Any]], None],
        priority: int = 100,
    ) -> None:
        """
        Register a callback for an event.

        Args:
            event_name: The event to listen for, or "*" for all events.
            callback:   Function accepting a single dict payload argument.
            priority:   Lower numbers are called first. Default 100.
                        Use 0 for latency-critical subscribers (e.g. WindowManager).
        """
        if event_name not in self._subscribers:
            self._subscribers[event_name] = []

        self._subscribers[event_name].append((priority, callback))

        # ──────────────────────────────────────────────────────────────
        # Re-sort by priority after each subscribe. This is O(n log n)
        # but only happens during initialization (once per subscriber),
        # not during the 60fps hot loop.
        # ──────────────────────────────────────────────────────────────
        self._subscribers[event_name].sort(key=lambda x: x[0])

        logger.debug(
            "Subscribed %s to '%s' at priority %d",
            callback.__qualname__,
            event_name,
            priority,
        )

    def unsubscribe(self, event_name: str, callback: Callable) -> None:
        """
        Remove a specific callback from an event channel.

        Silently does nothing if the callback isn't found — this prevents
        teardown-order bugs during shutdown.
        """
        if event_name in self._subscribers:
            self._subscribers[event_name] = [
                (p, cb)
                for p, cb in self._subscribers[event_name]
                if cb is not callback
            ]
            logger.debug(
                "Unsubscribed %s from '%s'",
                callback.__qualname__,
                event_name,
            )

    def publish(self, event_name: str, payload: dict[str, Any] | None = None) -> None:
        """
        Dispatch an event to all subscribers synchronously.

        The payload is a plain dict — no copies are made for performance.
        Subscribers MUST NOT mutate the payload dict. This is a contract,
        not enforced by code, because deep-copying 60 times per second
        would be wasteful.

        Args:
            event_name: The event being broadcast.
            payload:    Optional dict of event data. Defaults to empty dict.
        """
        if payload is None:
            payload = {}

        # ──────────────────────────────────────────────────────────────
        # Dispatch to specific subscribers first, then wildcard "*"
        # subscribers. Wildcard subscribers always run after specific
        # ones — they're observers, not participants.
        # ──────────────────────────────────────────────────────────────
        specific = self._subscribers.get(event_name, [])
        wildcard = self._subscribers.get("*", [])

        for _priority, callback in specific:
            try:
                callback(payload)
            except Exception:
                # ──────────────────────────────────────────────────────
                # NEVER let a subscriber crash the bus. Log and continue.
                # B must stay alive no matter what — he's a soul, not a
                # service that can afford to restart.
                # ──────────────────────────────────────────────────────
                logger.exception(
                    "Subscriber %s crashed on event '%s'",
                    callback.__qualname__,
                    event_name,
                )

        for _priority, callback in wildcard:
            try:
                callback(payload)
            except Exception:
                logger.exception(
                    "Wildcard subscriber %s crashed on event '%s'",
                    callback.__qualname__,
                    event_name,
                )
