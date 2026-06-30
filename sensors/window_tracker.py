"""
sensors/window_tracker.py — Hook-Based Window Focus Monitor
═══════════════════════════════════════════════════════════════════
Zero-latency active window tracking via Windows SetWinEventHook API.

NO polling. NO sleep loops. Pure event-driven OS hook on EVENT_SYSTEM_FOREGROUND.

Architecture:
  • Hook Thread:     Owns the Win32 message pump + WinEvent callback.
                     Does ONE thing: receives hwnd and pushes to queue.
  • Dispatch Thread: Reads the queue, builds rich WindowInfo, publishes to bus.
                     Isolated from the message loop — can block freely.

This separation guarantees hook callbacks are never delayed by Python work,
preventing Windows from silently unhooking us due to callback latency.

Publishes:
  active_window_changed → {hwnd, title, process_name, process_id,
                           window_class, app_type, timestamp}
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import psutil
import win32gui
import win32process

logger = logging.getLogger("B.sensors.window_tracker")

# ═══════════════════════════════════════════════════════════════════════════════
# Win32 Bindings
# ═══════════════════════════════════════════════════════════════════════════════

_user32   = ctypes.windll.user32
_ole32    = ctypes.windll.ole32
_kernel32 = ctypes.windll.kernel32

WINEVENT_OUTOFCONTEXT   = 0x0000  # Callback runs in caller's context (needs msg pump)
WINEVENT_SKIPOWNPROCESS = 0x0002  # Don't fire for our own process
EVENT_SYSTEM_FOREGROUND = 0x0003  # Target: window gained foreground focus
WM_QUIT                 = 0x0012  # Posted to pump thread to stop the loop

WinEventProcType = ctypes.WINFUNCTYPE(
    None,
    ctypes.wintypes.HANDLE,   # hWinEventHook
    ctypes.wintypes.DWORD,    # event
    ctypes.wintypes.HWND,     # hwnd
    ctypes.wintypes.LONG,     # idObject
    ctypes.wintypes.LONG,     # idChild
    ctypes.wintypes.DWORD,    # dwEventThread
    ctypes.wintypes.DWORD,    # dwmsEventTime
)

# ═══════════════════════════════════════════════════════════════════════════════
# App Classification Map
# ═══════════════════════════════════════════════════════════════════════════════

PROCESS_TO_APP_TYPE: dict[str, str] = {
    # ── Chromium browsers
    "chrome.exe":           "browser",
    "msedge.exe":           "browser",
    "brave.exe":            "browser",
    "opera.exe":            "browser",
    "vivaldi.exe":          "browser",
    "thorium.exe":          "browser",
    # ── Gecko browsers
    "firefox.exe":          "browser",
    "waterfox.exe":         "browser",
    "librewolf.exe":        "browser",
    # ── Code editors (Electron / native)
    "code.exe":             "code_editor",       # VS Code
    "code - insiders.exe":  "code_editor",
    "cursor.exe":           "code_editor",        # Cursor AI
    "windsurf.exe":         "code_editor",
    "notepad++.exe":        "code_editor",
    "sublime_text.exe":     "code_editor",
    "atom.exe":             "code_editor",
    "zed.exe":              "code_editor",
    # ── Plain text editors
    "notepad.exe":          "text_editor",
    "wordpad.exe":          "text_editor",
    "gedit.exe":            "text_editor",
    # ── IDEs (heavy)
    "devenv.exe":           "ide",               # Visual Studio
    "pycharm64.exe":        "ide",
    "idea64.exe":           "ide",               # IntelliJ
    "clion64.exe":          "ide",
    "rider64.exe":          "ide",
    "webstorm64.exe":       "ide",
    "goland64.exe":         "ide",
    "rubymine64.exe":       "ide",
    "phpstorm64.exe":       "ide",
    # ── Terminals
    "windowsterminal.exe":  "terminal",
    "wt.exe":               "terminal",
    "cmd.exe":              "terminal",
    "powershell.exe":       "terminal",
    "pwsh.exe":             "terminal",
    "alacritty.exe":        "terminal",
    "mintty.exe":           "terminal",
    "conhost.exe":          "terminal",
    "hyper.exe":            "terminal",
    # ── Microsoft Office
    "winword.exe":          "office_word",
    "excel.exe":            "office_excel",
    "powerpnt.exe":         "office_ppt",
    "onenote.exe":          "office_note",
    "outlook.exe":          "email_client",
    # ── Communication / collaboration
    "slack.exe":            "chat",
    "teams.exe":            "chat",
    "msteams.exe":          "chat",
    "discord.exe":          "chat",
    "zoom.exe":             "video_call",
    "webex.exe":            "video_call",
    # ── PDF viewers
    "acrord32.exe":         "pdf_viewer",
    "acrobat.exe":          "pdf_viewer",
    "sumatrapdf.exe":       "pdf_viewer",
    "foxitpdfeditor.exe":   "pdf_viewer",
}

# Windows / processes to silently ignore
IGNORED_TITLES: frozenset[str] = frozenset({
    "",
    "Program Manager",
    "Task View",
    "Search",
    "Start",
    "Windows Shell Experience Host",
    "Microsoft Text Input Application",
    "Action center",
    "Taskbar",
    "Desktop",
})

IGNORED_PROCESSES: frozenset[str] = frozenset({
    "explorer.exe",
    "taskmgr.exe",
    "systemsettings.exe",
    "searchui.exe",
    "shellexperiencehost.exe",
    "startmenuexperiencehost.exe",
    "lockapp.exe",
    "screensaver.exe",
})


# ═══════════════════════════════════════════════════════════════════════════════
# Data Model
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=False, eq=False)
class WindowInfo:
    hwnd:         int
    title:        str
    process_name: str
    process_id:   int
    window_class: str
    app_type:     str
    timestamp:    float = field(default_factory=time.monotonic)

    def is_same_content(self, other: "WindowInfo") -> bool:
        """
        True when two WindowInfo objects represent the same visual state.
        We use both hwnd AND title so in-place navigation (e.g. browser tab switch)
        still triggers a re-extraction even though the hwnd is the same.
        """
        return self.hwnd == other.hwnd and self.title == other.title

    def to_dict(self) -> dict:
        return {
            "hwnd":         self.hwnd,
            "title":        self.title,
            "process_name": self.process_name,
            "process_id":   self.process_id,
            "window_class": self.window_class,
            "app_type":     self.app_type,
            "timestamp":    self.timestamp,
        }

    def __str__(self) -> str:
        return (
            f"[{self.app_type.upper():<15}] "
            f"'{self.title[:60]}' "
            f"({self.process_name} pid={self.process_id})"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Window Metadata Builder
# ═══════════════════════════════════════════════════════════════════════════════

def _build_window_info(hwnd: int) -> Optional[WindowInfo]:
    """
    Extract rich metadata from a window handle.
    Returns None if the window should be ignored (desktop, taskbar, etc.)

    Designed to be fast:
      - All Win32 calls are synchronous in-process
      - psutil.Process() is cached by the OS for the same PID
      - No I/O, no network
    """
    try:
        title = win32gui.GetWindowText(hwnd)
        if not title or title in IGNORED_TITLES:
            logger.debug("Skipped (ignored title): %r  hwnd=0x%X", title, hwnd)
            return None

        # GetClassName never raises for valid HWNDs
        window_class = win32gui.GetClassName(hwnd)

        # PID lookup via Win32 (zero kernel transition cost)
        _, pid = win32process.GetWindowThreadProcessId(hwnd)

        try:
            proc         = psutil.Process(pid)
            process_name = proc.name().lower()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            process_name = "unknown"
            pid          = 0

        if process_name in IGNORED_PROCESSES:
            logger.debug("Skipped (ignored process): %s  hwnd=0x%X", process_name, hwnd)
            return None

        app_type = PROCESS_TO_APP_TYPE.get(process_name, "unknown")

        return WindowInfo(
            hwnd=hwnd,
            title=title,
            process_name=process_name,
            process_id=pid,
            window_class=window_class,
            app_type=app_type,
        )

    except Exception as exc:
        logger.debug("_build_window_info failed for hwnd=0x%X: %s", hwnd, exc)
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# WindowTracker
# ═══════════════════════════════════════════════════════════════════════════════

class WindowTracker:
    """
    Zero-latency window focus tracker.

    Lifecycle:
      start() → installs WinEvent hook + starts threads
      stop()  → unhooks + drains threads cleanly

    Tuning:
      DEBOUNCE_SECS:  Ignore rapid Alt+Tab flicker (default 150ms)
    """

    DEBOUNCE_SECS = 0.15   # 150ms debounce on rapid focus changes

    def __init__(self, bus):
        self._bus                  = bus
        self._running              = False
        self._current_window:      Optional[WindowInfo] = None
        self._event_queue:         queue.Queue[Optional[int]] = queue.Queue(maxsize=64)
        self._hook_handle          = None
        self._hook_proc            = None         # ← CRITICAL: keep reference to prevent GC
        self._hook_thread_win32_id = 0
        self._last_event_mono      = 0.0

        self._hook_thread = threading.Thread(
            target=self._hook_loop,
            daemon=True,
            name="WindowTracker.HookPump",
        )
        self._dispatch_thread = threading.Thread(
            target=self._dispatch_loop,
            daemon=True,
            name="WindowTracker.Dispatcher",
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            logger.warning("WindowTracker already running — ignoring start()")
            return
        self._running = True
        self._dispatch_thread.start()
        self._hook_thread.start()
        logger.info(
            "WindowTracker started | strategy=WinEventHook | debounce=%dms",
            int(self.DEBOUNCE_SECS * 1000),
        )

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        # Wake the dispatcher thread with a sentinel
        self._event_queue.put(None)
        # Tell the Win32 message pump to exit via WM_QUIT
        if self._hook_thread_win32_id:
            _user32.PostThreadMessageW(
                self._hook_thread_win32_id, WM_QUIT, 0, 0
            )
        logger.info("WindowTracker stop requested — draining threads")

    @property
    def current_window(self) -> Optional[WindowInfo]:
        """Thread-safe read of the last known focused window."""
        return self._current_window

    # ── Hook Thread (Win32 message pump) ─────────────────────────────────────

    def _hook_loop(self) -> None:
        """
        Dedicated thread that OWNS the WinEvent hook.

        MUST call CoInitialize before SetWinEventHook.
        MUST run a GetMessage loop — WINEVENT_OUTOFCONTEXT callbacks are
        dispatched through the message queue of the thread that installed the hook.
        """
        _ole32.CoInitialize(None)
        self._hook_thread_win32_id = _kernel32.GetCurrentThreadId()
        logger.debug("Hook thread alive | win32_tid=%d", self._hook_thread_win32_id)

        # ── Bootstrap: snapshot current foreground immediately ────────────────
        initial_hwnd = _user32.GetForegroundWindow()
        if initial_hwnd:
            logger.debug("Bootstrapping with current foreground hwnd=0x%X", initial_hwnd)
            self._event_queue.put(initial_hwnd)

        # ── Install hook ──────────────────────────────────────────────────────
        # Store the ctypes function pointer on self — Python's GC will free it
        # the moment the local variable goes out of scope otherwise.
        self._hook_proc = WinEventProcType(self._win_event_callback)

        self._hook_handle = _user32.SetWinEventHook(
            EVENT_SYSTEM_FOREGROUND,
            EVENT_SYSTEM_FOREGROUND,
            None,                                           # hmodWinEventProc (in-process → None)
            self._hook_proc,
            0,                                              # idProcess: all processes
            0,                                              # idThread:  all threads
            WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS,
        )

        if not self._hook_handle:
            err = _kernel32.GetLastError()
            logger.error(
                "SetWinEventHook FAILED (GetLastError=%d) — activating polling fallback", err
            )
            _ole32.CoUninitialize()
            self._polling_fallback()
            return

        logger.info(" WinEvent hook installed (handle=0x%X)", self._hook_handle)

        # ── Message pump ──────────────────────────────────────────────────────
        msg = ctypes.wintypes.MSG()
        while self._running:
            result = _user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if result == 0 or result == -1:   # 0 = WM_QUIT, -1 = error
                break
            _user32.TranslateMessage(ctypes.byref(msg))
            _user32.DispatchMessageW(ctypes.byref(msg))

        # ── Cleanup ───────────────────────────────────────────────────────────
        if self._hook_handle:
            _user32.UnhookWinEvent(self._hook_handle)
            self._hook_handle = None
            logger.debug("WinEvent hook removed cleanly")

        _ole32.CoUninitialize()
        logger.debug("Hook thread exiting")

    def _win_event_callback(
        self,
        hWinEventHook, event, hwnd,
        idObject, idChild, dwEventThread, dwmsEventTime,
    ) -> None:
        """
        Win32 WinEvent callback — runs on the hook thread inside the msg pump.
        KEEP THIS MINIMAL. Any Python work here delays the message loop.

        Only job: debounce + push hwnd to the queue.
        """
        if not hwnd:
            return

        now = time.monotonic()
        if (now - self._last_event_mono) < self.DEBOUNCE_SECS:
            logger.debug(
                "Debounced rapid focus change (hwnd=0x%X, Δ=%.0fms)",
                hwnd, (now - self._last_event_mono) * 1000,
            )
            return
        self._last_event_mono = now

        try:
            self._event_queue.put_nowait(hwnd)
        except queue.Full:
            logger.debug("Event queue full — dropping focus event (hwnd=0x%X)", hwnd)

    def _polling_fallback(self) -> None:
        """
        Emergency fallback if WinEvent hook installation fails.
        Polls at 2s — much higher latency but same functional output.
        """
        logger.warning(" [WARNING]   POLLING FALLBACK active (2s interval) — hook unavailable")
        while self._running:
            hwnd = _user32.GetForegroundWindow()
            if hwnd:
                try:
                    self._event_queue.put_nowait(hwnd)
                except queue.Full:
                    pass
            time.sleep(2.0)

    # ── Dispatch Thread ───────────────────────────────────────────────────────

    def _dispatch_loop(self) -> None:
        """
        Consumes hwnd events from the queue, enriches them into WindowInfo objects,
        and publishes to the bus. Runs independently of the Win32 message loop —
        can block, do I/O, and call psutil freely.
        """
        logger.debug("Dispatch loop started")
        while self._running:
            try:
                hwnd = self._event_queue.get(timeout=1.0)
            except queue.Empty:
                continue

            if hwnd is None:   # Sentinel → graceful stop
                break

            self._handle_focus_event(hwnd)

        logger.debug("Dispatch loop exiting")

    def _handle_focus_event(self, hwnd: int) -> None:
        t_start = time.monotonic()

        info = _build_window_info(hwnd)
        if info is None:
            return   # Silently ignored (desktop, taskbar, etc.)

        # Skip re-publish if the window content hasn't changed
        if self._current_window and info.is_same_content(self._current_window):
            logger.debug(
                "Same window content — not re-publishing  (hwnd=0x%X title=%r)",
                hwnd, info.title,
            )
            return

        prev                 = self._current_window
        self._current_window = info
        elapsed_ms           = (time.monotonic() - t_start) * 1000

        logger.info(" FOCUS → %s  [%.1f ms]", info, elapsed_ms)
        if prev:
            logger.debug("   ← was: %s", prev)

        self._bus.publish("active_window_changed", info.to_dict())