"""
main.py — B's Entry Point: Wiring the Soul
═══════════════════════════════════════════

This is where B comes to life. It creates the EventBus, instantiates
every module, wires them together (through the bus — never directly),
and starts the 60fps tick loop.

Architecture:
    1. Create QApplication (Qt event loop host)
    2. Instantiate EventBus (the central nervous system)
    3. Query screen geometry (for physics boundaries)
    4. Instantiate modules: KinematicsEngine, WindowManager, FaceRenderer, StateMachine
    5. Start a QTimer at 60fps that publishes "tick" events
    6. Register a global kill switch (Ctrl+Shift+Alt+Q)
    7. Enter Qt's event loop — B is alive
No module knows about any other module. They only know the bus.
The main loop is the only place where all modules are referenced,
and only for instantiation — never for cross-module calls.
"""

from __future__ import annotations

import logging
import sys
import time
import os

os.environ["QT_LOGGING_RULES"] = "qt.qpa.window=false"

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import QApplication

# ──────────────────────────────────────────────────────────────────────
# Module imports — each module is imported independently.
# They do NOT import each other. Ever.
# ──────────────────────────────────────────────────────────────────────
from core.bus import EventBus
from physics.kinematics import KinematicsEngine
from ui.overlay import WindowManager
from brain.llm import CognitiveEngine
from brain.soul import StateMachine
from brain.autonomy_loop import AutonomyEngine
from vision.semantic import SemanticVisionSensor
from sensors.window_tracker import WindowTracker
from audio.speaker import VoiceEngine
from ui.chat import ChatBubble
from ui.input_box import InputBox
from audio.ears import EarsSensor

# ──────────────────────────────────────────────────────────────────────
# Logging configuration — structured logging for all modules.
# Each module uses logging.getLogger ("B.<module>") so we can filter
# per-subsystem. Set to INFO for normal operation, DEBUG for development.
# ──────────────────────────────────────────────────────────────────────
# Filter out repetitive or unnecessary third-party logs
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

log_formatter = logging.Formatter(
    fmt="%(asctime)s │ %(name)-20s │ %(levelname)-5s │ %(message)s",
    datefmt="%H:%M:%S"
)

# Persistent history log
history_handler = logging.FileHandler("b_analytics.log", mode="a", encoding="utf-8")
history_handler.setFormatter(log_formatter)

# Current session log (cleared on start)
session_handler = logging.FileHandler("b_session.log", mode="w", encoding="utf-8")
session_handler.setFormatter(log_formatter)

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(log_formatter)

class ConsoleFilter(logging.Filter):
    def filter(self, record):
        if record.levelno >= logging.WARNING:
            return True
        if record.name in ("B.vision.semantic", "B.vision.capture", "B.sensors.window_tracker", "B.brain.autonomy"):
            return False
        if record.name == "B.brain.llm" and "Cognitive Context:" in record.getMessage():
            return False
        return True

console_handler.addFilter(ConsoleFilter())

logging.basicConfig(
    level=logging.INFO,
    handlers=[history_handler, session_handler, console_handler]
)
logger = logging.getLogger("B.main")

# ──────────────────────────────────────────────────────────────────────
# Tick rate — 60fps. This is the heartbeat of B's existence.
# 16ms per tick. Every module that subscribes to "tick" runs at this
# cadence. If the system can't keep up, ticks are dropped (Qt coalesces
# timer events), but B stays smooth — he just moves slower.
# ──────────────────────────────────────────────────────────────────────
TICK_INTERVAL_MS = 16  # ~60fps


def main() -> None:
    """
    Boot sequence for B.

    This function is the ONLY place where modules are instantiated
    and connected. After this function returns (via app.exec()),
    B is gone.
    """

    # ──────────────────────────────────────────────────────────────────
    # Step 1: Create Qt application
    #
    # QApplication MUST be created before any QWidget. It owns the
    # event loop, the display connection, and all OS-level resources.
    # ──────────────────────────────────────────────────────────────────
    app = QApplication(sys.argv)
    app.setApplicationName("B")
    logger.info("═══ B is waking up ═══")

    # ──────────────────────────────────────────────────────────────────
    # Step 2: Create the EventBus — the central nervous system
    # ──────────────────────────────────────────────────────────────────
    bus = EventBus()

    # ──────────────────────────────────────────────────────────────────
    # Step 3: Query screen geometry
    #
    # We pass the screen rect into KinematicsEngine so it knows where
    # the edges are. This adapts to whatever display is active —
    # laptop screen, external monitor, multi-monitor setups.
    #
    # availableGeometry() returns the usable area (excludes taskbar).
    # ──────────────────────────────────────────────────────────────────
    primary_screen = app.primaryScreen()
    if primary_screen is not None:
        screen_rect = primary_screen.availableGeometry()
        logger.info(
            "Screen geometry: %dx%d at (%d,%d)",
            screen_rect.width(),
            screen_rect.height(),
            screen_rect.x(),
            screen_rect.y(),
        )
    else:
        # Should never happen on a real system, but defensive coding
        from PyQt6.QtCore import QRect
        screen_rect = QRect(0, 0, 1920, 1080)
        logger.warning("No primary screen detected, using 1920x1080 fallback")

    # ──────────────────────────────────────────────────────────────────
    # Step 4: Instantiate modules
    #
    # Order matters slightly: WindowManager must exist before
    # FaceRenderer (parent-child relationship). But all bus
    # subscriptions are order-independent.
    # ──────────────────────────────────────────────────────────────────

    # 4a. The physics engine — computes where B should be
    kinematics = KinematicsEngine(bus, screen_rect)

    # 4b. The window — the transparent ghost overlay
    window = WindowManager(bus)

    # 4c. The face — rendered as a child widget inside the window
    from ui.face import FaceRenderer
    face = FaceRenderer(bus, parent=window)

    # 4d. The chat bubble and input box
    chat = ChatBubble(bus, screen_rect)
    input_box = InputBox(bus, screen_rect)

    # 4e. The soul — personality and emotional state
    soul = StateMachine(bus)
    
    # 4f. The brain — LLM inference
    llm = CognitiveEngine(bus)

    # 4f. The eyes — semantic UI extraction
    vision_semantic = SemanticVisionSensor(bus)
    vision_semantic.start()
    
    # 4f-2. The eyes — OCR fallback (mss)
    from vision.mss_capture import VisionSensor as OCRVision
    vision_ocr = OCRVision(bus)
    
    # 4g. The focus — active window tracking (hook-based)
    window_tracker = WindowTracker(bus)
    window_tracker.start()

    # 4h. The brain's clock — curiosity and proactivity
    autonomy = AutonomyEngine(bus)

    # 4i. The voice — local TTS
    voice = VoiceEngine(bus)
    
    # 4j. The ears — Speech-to-Text
    ears = EarsSensor(bus)

    # ──────────────────────────────────────────────────────────────────
    # Step 5: Show the window and apply OS-level click-through
    #
    # initialize() calls show() + Win32 WS_EX_TRANSPARENT.
    # This MUST happen after all child widgets are attached.
    # ──────────────────────────────────────────────────────────────────
    window.initialize()
    chat.initialize()
    logger.info("Window visible and click-through enabled")

    # ──────────────────────────────────────────────────────────────────
    # Step 6: Global kill switch — Ctrl+Shift+Alt+Q
    #
    # CRITICAL SAFETY MECHANISM
    #
    # Because we set WS_EX_TRANSPARENT (click-through) and Tool
    # (hidden from taskbar/Alt+Tab), there is NO standard OS way
    # to close B. No X button, no taskbar icon, no Alt+F4 target.
    #
    # If the physics engine has a bug and B starts vibrating wildly,
    # or if you just need to kill him quickly, this hotkey is your
    # emergency exit.
    #
    # We use QShortcut on the window. Even though the window is
    # click-through, keyboard shortcuts still work because they're
    # registered at the Qt level, not the Win32 level.
    #
    # NOTE: QShortcut on a click-through Tool window may not reliably
    # capture global keys. As a belt-and-suspenders fallback, we also
    # register a secondary approach: Ctrl+Shift+Alt+Q via a tray-less
    # mechanism. For Sprint 1, the QShortcut approach works because Qt
    # still processes keyboard events on the window's context.
    # ──────────────────────────────────────────────────────────────────
    # Fallback: use a simple polling approach for the kill switch
    # since QShortcut may not work on click-through windows.
    _setup_hotkeys(app, input_box, ears, bus)

    # ──────────────────────────────────────────────────────────────────
    # Step 7: Start the 60fps tick timer
    #
    # QTimer fires every 16ms, publishing a "tick" event on the bus.
    # Every subscribed module (KinematicsEngine, StateMachine) responds.
    # This is B's heartbeat — as long as the timer ticks, B lives.
    # ──────────────────────────────────────────────────────────────────
    last_tick_time = time.monotonic()

    def on_timer() -> None:
        """Publish a tick event with accurate delta time."""
        nonlocal last_tick_time
        now = time.monotonic()
        dt = now - last_tick_time
        last_tick_time = now
        bus.publish("tick", {"dt": dt})

    tick_timer = QTimer()
    tick_timer.setTimerType(Qt.TimerType.PreciseTimer)
    tick_timer.timeout.connect(on_timer)
    tick_timer.start(TICK_INTERVAL_MS)

    logger.info("Tick timer started (%dms / ~%dfps)", TICK_INTERVAL_MS, 1000 // TICK_INTERVAL_MS)
    logger.info("═══ B is alive ═══")
    logger.info("Kill switch: Ctrl+Shift+Alt+Q (press in any window)")

    # ──────────────────────────────────────────────────────────────────
    # Step 8: Enter the event loop — B lives until quit is called
    # ──────────────────────────────────────────────────────────────────
    exit_code = app.exec()

    logger.info("═══ B is sleeping ═══ (exit code: %d)", exit_code)
    sys.exit(exit_code)


def _setup_hotkeys(app: QApplication, input_box: InputBox, ears: EarsSensor, bus: EventBus) -> None:
    """
    Register global hotkeys via Win32 RegisterHotKey.
    
    Ctrl+Shift+Alt+Q -> Kill switch
    Ctrl+Shift+Alt+B -> Toggle input box
    Ctrl+Shift+Alt+S -> Toggle speak mode (ears)
    """
    if sys.platform != "win32":
        logger.warning("Hotkeys only implemented for Windows")
        return

    try:
        import ctypes
        from ctypes import wintypes
        import win32con

        MOD_CTRL_SHIFT_ALT = (
            win32con.MOD_CONTROL | win32con.MOD_SHIFT | win32con.MOD_ALT
        )
        
        HOTKEY_KILL = 1
        HOTKEY_INPUT = 2
        HOTKEY_SPEAK = 3
        HOTKEY_WORK = 4
        VK_Q = 0x51
        VK_B = 0x42
        VK_V = 0x56
        VK_W = 0x57

        # Kill Switch
        result_kill = ctypes.windll.user32.RegisterHotKey(
            None, HOTKEY_KILL, MOD_CTRL_SHIFT_ALT, VK_Q
        )
        if not result_kill:
            logger.warning("Failed to register kill switch hotkey")

        # Input Box
        result_input = ctypes.windll.user32.RegisterHotKey(
            None, HOTKEY_INPUT, MOD_CTRL_SHIFT_ALT, VK_B
        )
        if not result_input:
            logger.warning("Failed to register input box hotkey")
            
        result_speak = ctypes.windll.user32.RegisterHotKey(
            None, HOTKEY_SPEAK, MOD_CTRL_SHIFT_ALT, VK_V
        )
        if not result_speak:
            logger.warning("Failed to register speak mode hotkey")

        # Work Mode
        result_work = ctypes.windll.user32.RegisterHotKey(
            None, HOTKEY_WORK, MOD_CTRL_SHIFT_ALT, VK_W
        )
        if not result_work:
            logger.warning("Failed to register work mode hotkey")

        # Keep references to prevent garbage collection
        app._hotkey_kill = HOTKEY_KILL
        app._hotkey_input = HOTKEY_INPUT
        app._hotkey_speak = HOTKEY_SPEAK
        app._hotkey_work = HOTKEY_WORK
        app._input_box = input_box
        app._ears = ears
        app._bus = bus
        app._speak_mode = False
        app._work_mode = False
        
        from PyQt6.QtCore import QAbstractNativeEventFilter
        class HotkeyFilter(QAbstractNativeEventFilter):
            def nativeEventFilter(self, eventType, message):
                # In PyQt6, message is a sip.voidptr
                msg = ctypes.cast(int(message), ctypes.POINTER(wintypes.MSG)).contents
                if msg.message == 0x0312:  # WM_HOTKEY
                    if msg.wParam == app._hotkey_kill:
                        logger.info("═══ Kill switch activated ═══")
                        ctypes.windll.user32.UnregisterHotKey(None, app._hotkey_kill)
                        ctypes.windll.user32.UnregisterHotKey(None, app._hotkey_input)
                        app.quit()
                        return True, 0
                    elif msg.wParam == app._hotkey_input:
                        app._input_box.toggle()
                        return True, 0
                    elif msg.wParam == app._hotkey_speak:
                        app._speak_mode = not app._speak_mode
                        app._bus.publish("b_speak_mode_toggled", {"active": app._speak_mode})
                        if app._speak_mode:
                            logger.info("🎤 Speak Mode: ON")
                            app._ears.start_listening()
                        else:
                            logger.info("🎤 Speak Mode: OFF")
                            app._ears.stop_listening()
                        return True, 0
                    elif msg.wParam == app._hotkey_work:
                        app._work_mode = not app._work_mode
                        app._bus.publish("b_work_mode_toggled", {"active": app._work_mode})
                        if app._work_mode:
                            logger.info("💼 Work Mode: ON")
                        else:
                            logger.info("💼 Work Mode: OFF")
                        return True, 0
                return False, 0
        
        app._hotkey_filter = HotkeyFilter()
        app.installNativeEventFilter(app._hotkey_filter)

        logger.info("Hotkeys registered: Kill (Ctrl+Shift+Alt+Q), Input (Ctrl+Shift+Alt+B), Speak (Ctrl+Shift+Alt+V), Work (Ctrl+Shift+Alt+W)")

    except Exception:
        logger.exception("Failed to set up hotkeys")


if __name__ == "__main__":
    main()
