from __future__ import annotations

import logging
from typing import Any, Callable
from PyQt6.QtCore import QObject, pyqtSignal, QThread, QCoreApplication

logger = logging.getLogger("B.core.bus")

class EventBus(QObject):
    """
    A thread-aware, synchronous/asynchronous hybrid publish/subscribe event bus.
    
    The EventBus is the ONLY way modules communicate.
    Design constraints:
        1. Thread Safety: Can be published to from any thread.
        2. UI Safety: Guaranteed dispatch on the Main Thread for UI consistency.
        3. Priority Ordering: Subscribers are called in order of priority.
    """
    
    # Internal signal used to bridge threads
    _cross_thread_signal = pyqtSignal(str, dict)

    def __init__(self) -> None:
        super().__init__()
        # event_name -> list of (priority, callback)
        self._subscribers: dict[str, list[tuple[int, Callable]]] = {}
        
        # Connect the bridge signal to the safe dispatch method
        self._cross_thread_signal.connect(self._safe_dispatch)
        
        logger.info("Thread-Aware EventBus initialized")

    def subscribe(
        self,
        event_name: str,
        callback: Callable[[dict[str, Any]], None],
        priority: int = 100,
    ) -> None:
        """Register a callback for an event."""
        if event_name not in self._subscribers:
            self._subscribers[event_name] = []

        self._subscribers[event_name].append((priority, callback))
        self._subscribers[event_name].sort(key=lambda x: x[0])

        logger.debug("Subscribed %s to '%s' (Priority %d)", callback.__qualname__, event_name, priority)

    def unsubscribe(self, event_name: str, callback: Callable) -> None:
        """Remove a specific callback from an event channel."""
        if event_name in self._subscribers:
            self._subscribers[event_name] = [
                (p, cb) for p, cb in self._subscribers[event_name] if cb is not callback
            ]

    def publish(self, event_name: str, payload: dict[str, Any] | None = None) -> None:
        """
        Dispatch an event to all subscribers.
        If called from a background thread, it automatically redirects execution 
        to the Main Thread via Qt's signal/slot mechanism.
        """
        if payload is None:
            payload = {}

        # Check if we are on the main (GUI) thread
        main_thread = QCoreApplication.instance().thread()
        if QThread.currentThread() != main_thread:
            # Not on main thread: use signal to jump threads
            self._cross_thread_signal.emit(event_name, payload)
        else:
            # Already on main thread: direct call for zero latency
            self._safe_dispatch(event_name, payload)

    def _safe_dispatch(self, event_name: str, payload: dict[str, Any]) -> None:
        """The actual dispatch logic, guaranteed to run on the Main Thread."""
        specific = self._subscribers.get(event_name, [])
        wildcard = self._subscribers.get("*", [])

        # Priority-ordered dispatch
        for _priority, callback in specific:
            try:
                callback(payload)
            except Exception:
                logger.exception("Subscriber %s crashed on event '%s'", callback.__qualname__, event_name)

        # Wildcard observers
        for _priority, callback in wildcard:
            try:
                callback(payload)
            except Exception:
                logger.exception("Wildcard subscriber %s crashed on event '%s'", callback.__qualname__, event_name)
