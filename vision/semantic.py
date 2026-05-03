"""
sensors/vision_semantic.py — Smart Semantic Context Engine
══════════════════════════════════════════════════════════════

Primary content extraction. Uses UIAutomation accessibility tree to read
structured text directly from any app — zero CPU, instant, no OCR lag.

Extraction Pipeline (per window focus event):
  Step 1  Acquire UIAutomation handle for the hwnd
  Step 2  Run app-specific strategy  (browser / code_editor / terminal / office / generic)
  Step 3  If quality < threshold → run generic tree-walk as secondary attempt
  Step 4  Quality gate — emit semantic_extraction_failed if still poor (→ OCR fallback)
  Step 5  Smart truncation (head + center + tail, not blind first-N-chars)
  Step 6  MD5 deduplication — skip publish if content unchanged
  Step 7  Publish context_updated with rich metadata

Strategy Pattern:
  Each app type has a dedicated extractor that knows the UIA tree layout
  of that app category. Generic tree-walk scores all controls and picks best.

Content Quality Scorer:
  Penalises:  short text, low char variety, UI-chrome words (Close, Minimize…)
  Rewards:    long text, high character variety (real prose / code)

Publishes:
  context_updated            → {window_title, screen_text, app_type,
                                extraction_source, quality_score,
                                content_length, snippet_length, extraction_ms, …}
  semantic_extraction_failed → {hwnd, title, app_type, failure_reason, …}
"""
from __future__ import annotations

import hashlib
import logging
import math
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import uiautomation as auto

logger = logging.getLogger("B.vision.semantic")

# ═══════════════════════════════════════════════════════════════════════════════
# Tunable Constants
# ═══════════════════════════════════════════════════════════════════════════════

SNIPPET_MAX_CHARS  = 4_000   # Max characters shipped downstream per update
MIN_QUALITY_CHARS  = 30      # Text shorter than this is treated as extraction failure
TREE_MAX_DEPTH     = 5       # Generic strategy: max UIA tree walk depth
TREE_MAX_NODES     = 100     # Generic strategy: max nodes visited per walk (reduced for speed)
RATE_LIMIT_SECS    = 4.0     # Background frequency (anti-spam, user-requested reduction)
UIA_TIMEOUT        = 1.5     # UIAutomation global search timeout (seconds)

# ── UIA ControlType routing sets ─────────────────────────────────────────────
_CT = auto.ControlType
CONTENT_TYPES = frozenset({
    _CT.DocumentControl, _CT.EditControl,
    _CT.TextControl,     _CT.PaneControl,
    _CT.DataGridControl, _CT.ListControl,
    _CT.TreeControl,
})
SKIP_TYPES = frozenset({
    _CT.MenuBarControl,  _CT.MenuControl,    _CT.MenuItemControl,
    _CT.ToolBarControl,  _CT.StatusBarControl, _CT.ScrollBarControl,
    _CT.TitleBarControl, _CT.SeparatorControl, _CT.ThumbControl,
    _CT.HeaderControl,   _CT.HeaderItemControl,
})

# Common UI-chrome words that indicate we hit a menu/toolbar, not real content
_UI_CHROME_WORDS = frozenset({
    "close", "minimize", "maximize", "restore", "pin", "unpin",
    "back", "forward", "reload", "refresh", "home", "stop", "print",
    "file", "edit", "view", "help", "insert", "format", "tools",
    "window", "extensions", "new tab", "settings", "menu", "search",
    "ok", "cancel", "apply", "yes", "no", "open", "save", "undo", "redo",
})


# ═══════════════════════════════════════════════════════════════════════════════
# Data Model
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ExtractionResult:
    text:       str
    source:     str          # e.g. "browser_document", "tree_walk:EditControl"
    quality:    float        # 0.0–1.0 heuristic score
    elapsed_ms: float
    metadata:   dict = field(default_factory=dict)

    @property
    def is_good(self) -> bool:
        return len(self.text.strip()) >= MIN_QUALITY_CHARS and self.quality > 0.1

    def __str__(self) -> str:
        return (
            f"ExtractionResult("
            f"source={self.source!r}, q={self.quality:.2f}, "
            f"len={len(self.text)}, t={self.elapsed_ms:.1f}ms)"
        )


_EMPTY = ExtractionResult("", "none", 0.0, 0.0)


# ═══════════════════════════════════════════════════════════════════════════════
# Low-Level UIA Text Extractors
# ═══════════════════════════════════════════════════════════════════════════════

def _try_text_pattern(ctrl, max_chars: int = SNIPPET_MAX_CHARS) -> str:
    """
    TextPattern: best for rich text documents (Word, browsers).
    Returns the full document text range up to max_chars.
    """
    try:
        tp = ctrl.GetTextPattern()
        if tp:
            text = tp.DocumentRange.GetText(max_chars)
            if text:
                logger.debug("    TextPattern  → %d chars", len(text))
            return text or ""
    except Exception:
        pass
    return ""


def _try_value_pattern(ctrl) -> str:
    """
    ValuePattern: best for single-line inputs, code editors, form fields.
    Returns the current 'value' of the control.
    """
    try:
        vp = ctrl.GetValuePattern()
        val = vp.Value if vp else ""
        if val:
            logger.debug("    ValuePattern → %d chars", len(val))
        return val or ""
    except Exception:
        return ""


def _try_name(ctrl) -> str:
    """Last resort: accessible name property."""
    try:
        name = ctrl.Name or ""
        if name:
            logger.debug("    Name         → %d chars", len(name))
        return name
    except Exception:
        return ""


def _read_control(ctrl) -> str:
    """
    Attempt UIA text extraction using a priority-ordered chain.
    TextPattern > ValuePattern > Name.
    """
    # Debug: log available patterns for this control
    try:
        patterns = []
        if ctrl.GetTextPattern(): patterns.append("Text")
        if ctrl.GetValuePattern(): patterns.append("Value")
        if patterns:
            logger.debug("    Patterns: %s", ", ".join(patterns))
    except Exception:
        pass

    text = _try_text_pattern(ctrl)
    if not text:
        text = _try_value_pattern(ctrl)
    if not text:
        text = _try_name(ctrl)
    return text.strip()


# ═══════════════════════════════════════════════════════════════════════════════
# Content Quality Scorer
# ═══════════════════════════════════════════════════════════════════════════════

def _score(text: str) -> float:
    """
    Heuristic quality score → [0.0, 1.0].

    Model:
      score = length_score × variety_score × (1 − chrome_penalty)

      length_score:   log10 scale, saturates at 1.0 around 5000 chars
      variety_score:  unique_chars / sample_len × 2.2  (high variety = real content)
      chrome_penalty: fraction of words that are UI chrome labels × 0.6

    Examples:
      Real article paragraph (500 chars)   → ~0.82
      Browser toolbar label list           → ~0.18
      Single word "Close"                  → 0.0 (below MIN_QUALITY_CHARS)
    """
    stripped = text.strip()
    if not stripped or len(stripped) < MIN_QUALITY_CHARS:
        return 0.0

    n = len(stripped)

    # Length score (log scale)
    length_score = min(1.0, math.log10(n / 30) / math.log10(5_000 / 30))

    # Character variety (over first 200 chars)
    sample = stripped[:200]
    variety_score = min(1.0, (len(set(sample)) / len(sample)) * 2.2)

    # Chrome-word penalty
    words = stripped.lower().split()[:30]
    chrome_frac = sum(
        1 for w in words if w.rstrip(".,!?:;-") in _UI_CHROME_WORDS
    ) / max(len(words), 1)
    chrome_penalty = chrome_frac * 0.6

    score = max(0.0, min(1.0, length_score * variety_score * (1.0 - chrome_penalty)))
    logger.debug(
        "    Quality %.2f  (len=%.2f var=%.2f chrome_pen=%.2f)  for %d chars",
        score, length_score, variety_score, chrome_penalty, n,
    )
    return score


# ═══════════════════════════════════════════════════════════════════════════════
# App-Specific Extraction Strategies
# ═══════════════════════════════════════════════════════════════════════════════

def _strategy_browser(window) -> ExtractionResult:
    """
    Chromium / Firefox extraction.

    1. Address bar  (EditControl near top, depth≤5) → captures current URL.
    2. Document control (depth≤7) → captures rendered page text via TextPattern.

    Why DocumentControl at depth 7?
      Chrome's renderer is nested: BrowserFrame → BrowserView → WebView →
      RenderWidgetHostViewAura → DocumentControl. Depth 5 misses it on some builds.
    """
    t = time.monotonic()
    metadata: dict = {}
    logger.debug("[browser] Scanning address bar…")

    # ── URL ──────────────────────────────────────────────────────────────────
    try:
        addr = window.EditControl(searchDepth=5)
        if addr.Exists(0, 0):
            url = _try_value_pattern(addr)
            if url and ("://" in url or url.startswith("about:")):
                metadata["url"] = url
                logger.info("[browser] URL captured: %s", url[:120])
    except Exception as e:
        logger.debug("[browser] Address bar extraction error: %s", e)

    # ── Page content ──────────────────────────────────────────────────────────
    content = ""
    logger.debug("[browser] Searching for DocumentControl (depth≤12)…")
    try:
        # Chromium often nests the document deeply (Browser -> View -> WebView -> Renderer -> Document)
        # We increase searchDepth and try to find the one with actual content.
        docs = window.GetChildren() # Look at top-level children first to find the main view
        
        # Standard search
        doc = window.DocumentControl(searchDepth=12)
        if doc.Exists(0, 0):
            content = _read_control(doc)
            if content:
                logger.info("[browser] DocumentControl → %d chars", len(content))
        
        # If still empty, try walking specifically for DocumentControls
        if not content:
            logger.debug("[browser] Primary DocumentControl empty, walking for alternatives…")
            for d, depth in auto.WalkControl(window, maxDepth=12):
                if d.ControlType == _CT.DocumentControl:
                    text = _read_control(d)
                    if len(text) > len(content):
                        content = text
                        logger.info("[browser] Alternative DocumentControl (depth %d) → %d chars", depth, len(content))
                        if len(content) > 500: break # Found good content
    except Exception as e:
        logger.debug("[browser] DocumentControl error: %s", e)

    elapsed = (time.monotonic() - t) * 1000
    return ExtractionResult(
        text=content, source="browser_document",
        quality=_score(content), elapsed_ms=elapsed, metadata=metadata,
    )


def _strategy_code_editor(window) -> ExtractionResult:
    """
    VS Code / Cursor / Notepad++ / Sublime / JetBrains extraction.

    Priority:
      1. EditControl (Notepad++, Sublime, plain editors)
      2. DocumentControl (VS Code monaco, JetBrains)

    Also extracts the active filename from the window title pattern:
      "main.py — myproject — Visual Studio Code"
      "DatabaseMigration.java - IntelliJ IDEA"
    """
    t = time.monotonic()
    metadata: dict = {}
    content = ""

    # ── Active filename from title ────────────────────────────────────────────
    try:
        title = window.Name or ""
        for sep in ("—", "–", " - "):
            if sep in title:
                fname = title.split(sep)[0].strip()
                if fname:
                    metadata["active_file"] = fname
                    break
        if "active_file" in metadata:
            logger.info("[code_editor] Active file: %r", metadata["active_file"])
    except Exception:
        pass

    # ── Try EditControl ───────────────────────────────────────────────────────
    logger.debug("[code_editor] Trying EditControl (depth≤6)…")
    try:
        edit = window.EditControl(searchDepth=6)
        if edit.Exists(0, 0):
            content = _read_control(edit)
            logger.info("[code_editor] EditControl → %d chars", len(content))
    except Exception as e:
        logger.debug("[code_editor] EditControl failed: %s", e)

    # ── Try DocumentControl if EditControl came up short ─────────────────────
    if len(content) < MIN_QUALITY_CHARS:
        logger.debug("[code_editor] EditControl insufficient — trying DocumentControl…")
        try:
            doc = window.DocumentControl(searchDepth=6)
            if doc.Exists(0, 0):
                doc_text = _read_control(doc)
                if len(doc_text) > len(content):
                    content = doc_text
                    logger.info("[code_editor] DocumentControl → %d chars", len(content))
        except Exception as e:
            logger.debug("[code_editor] DocumentControl failed: %s", e)

    elapsed = (time.monotonic() - t) * 1000
    return ExtractionResult(
        text=content, source="code_editor_buffer",
        quality=_score(content), elapsed_ms=elapsed, metadata=metadata,
    )


def _strategy_terminal(window) -> ExtractionResult:
    """
    Windows Terminal / CMD / PowerShell extraction.

    Terminal output grows downward — the interesting content is at the TAIL
    of the scroll buffer. We read the full buffer and take the last 2500 chars.
    This captures the most recent command output without wasting tokens on
    the session history.
    """
    t = time.monotonic()
    content = ""
    full_len = 0
    source_name = "terminal_tail"

    logger.debug("[terminal] Searching for terminal content control…")
    for label, finder in [
        ("DocumentControl", lambda: window.DocumentControl(searchDepth=5)),
        ("EditControl",     lambda: window.EditControl(searchDepth=5)),
    ]:
        try:
            ctrl = finder()
            if ctrl.Exists(0, 0):
                raw = _read_control(ctrl)
                full_len = len(raw)
                # Take the tail — that's where the latest prompt + output is
                content = raw[-2_500:] if full_len > 2_500 else raw
                source_name = f"terminal_tail:{label}"
                logger.info(
                    "[terminal] %s → buffer=%d chars | using tail %d chars",
                    label, full_len, len(content),
                )
                break
        except Exception as e:
            logger.debug("[terminal] %s failed: %s", label, e)

    elapsed = (time.monotonic() - t) * 1000
    return ExtractionResult(
        text=content, source=source_name,
        quality=_score(content), elapsed_ms=elapsed,
        metadata={"full_buffer_length": full_len},
    )


def _strategy_office(window) -> ExtractionResult:
    """
    Microsoft Office (Word / Excel / PowerPoint / OneNote) extraction.
    All Office apps expose their content via a DocumentControl with TextPattern.
    """
    t = time.monotonic()
    content = ""

    logger.debug("[office] Searching for DocumentControl (depth≤5)…")
    try:
        doc = window.DocumentControl(searchDepth=5)
        if doc.Exists(0, 0):
            content = _read_control(doc)
            logger.info("[office] DocumentControl → %d chars", len(content))
    except Exception as e:
        logger.debug("[office] DocumentControl failed: %s", e)

    elapsed = (time.monotonic() - t) * 1000
    return ExtractionResult(
        text=content, source="office_document",
        quality=_score(content), elapsed_ms=elapsed, metadata={},
    )


def _strategy_generic(window) -> ExtractionResult:
    """
    Universal fallback: walk the UIA control tree, score each content control,
    return the single highest-quality block found.

    Algorithm:
      1. WalkControl with depth cap and node cap.
      2. Skip obvious chrome (menu bars, toolbars, status bars, scroll bars).
      3. Focus on CONTENT_TYPES (Document, Edit, Text, Pane, Grid, List).
      4. Score each candidate with _score().
      5. Return winner (highest score).

    This handles: chat apps, PDF viewers, email clients, custom apps, Electron apps
    that expose SOME accessibility but don't match a specific pattern.
    """
    t = time.monotonic()
    candidates: List[Tuple[float, str, str, int]] = []   # (score, text, type_name, depth)
    nodes_visited = 0

    logger.debug(
        "[generic] Tree walk | max_depth=%d max_nodes=%d",
        TREE_MAX_DEPTH, TREE_MAX_NODES,
    )

    try:
        for ctrl, depth in auto.WalkControl(window, maxDepth=TREE_MAX_DEPTH):
            nodes_visited += 1
            if nodes_visited > TREE_MAX_NODES:
                logger.debug("[generic] Node cap hit (%d) — stopping walk", TREE_MAX_NODES)
                break

            ctype = ctrl.ControlType
            if ctype in SKIP_TYPES:
                continue
            if ctype not in CONTENT_TYPES:
                continue

            text = _read_control(ctrl)
            if not text or len(text.strip()) < MIN_QUALITY_CHARS:
                continue

            s = _score(text)
            type_name = ctrl.ControlTypeName
            candidates.append((s, text, type_name, depth))
            
            # Optimization: If we found a very high quality block, we can stop early
            if s > 0.85:
                logger.debug("[generic] High quality winner found early (q=%.2f) — stopping walk", s)
                break
            logger.debug(
                "[generic]   → depth=%-2d  %-22s  q=%.2f  len=%d",
                depth, type_name, s, len(text),
            )

    except Exception as e:
        logger.debug("[generic] Tree walk exception: %s", e)

    elapsed = (time.monotonic() - t) * 1000
    logger.info(
        "[generic] Walk done: %d nodes visited, %d candidates  [%.1f ms]",
        nodes_visited, len(candidates), elapsed,
    )

    if not candidates:
        return ExtractionResult("", "generic:empty", 0.0, elapsed, {})

    # Pick the winner
    candidates.sort(key=lambda x: x[0], reverse=True)
    best_score, best_text, best_type, best_depth = candidates[0]

    logger.info(
        "[generic] Winner: %-22s  depth=%-2d  q=%.2f  len=%d",
        best_type, best_depth, best_score, len(best_text),
    )

    return ExtractionResult(
        text=best_text,
        source=f"generic_tree_walk:{best_type}",
        quality=best_score,
        elapsed_ms=elapsed,
        metadata={
            "nodes_visited":    nodes_visited,
            "candidates_found": len(candidates),
            "winning_type":     best_type,
            "winning_depth":    best_depth,
        },
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Strategy Registry
# ═══════════════════════════════════════════════════════════════════════════════

_STRATEGY_MAP: Dict[str, Callable] = {
    "browser":       _strategy_browser,
    "code_editor":   _strategy_code_editor,
    "ide":           _strategy_code_editor,
    "text_editor":   _strategy_code_editor,
    "terminal":      _strategy_terminal,
    "office_word":   _strategy_office,
    "office_excel":  _strategy_office,
    "office_ppt":    _strategy_office,
    "office_note":   _strategy_office,
    # Everything else → smart generic
    "email_client":  _strategy_generic,
    "chat":          _strategy_generic,
    "video_call":    _strategy_generic,
    "pdf_viewer":    _strategy_generic,
    "unknown":       _strategy_generic,
}


# ═══════════════════════════════════════════════════════════════════════════════
# Smart Truncation
# ═══════════════════════════════════════════════════════════════════════════════

def _smart_truncate(text: str, limit: int) -> str:
    """
    Preserve semantic structure when truncating long documents.

    Instead of naïvely taking the first N chars (which gives only the
    document header / boilerplate), we take a weighted window:

      Head  20%  — document title, top-of-page context
      Body  70%  — main content, centered in the document
      Tail  10%  — current position / recent additions

    Markers are inserted so downstream knows text was trimmed.
    """
    if len(text) <= limit:
        return text

    head_n = int(limit * 0.20)
    body_n = int(limit * 0.70)
    tail_n = limit - head_n - body_n

    head = text[:head_n]
    # Center the body window in the document
    mid_start = max(head_n, (len(text) - body_n) // 2)
    body = text[mid_start: mid_start + body_n]
    tail = text[-tail_n:] if tail_n > 0 else ""

    omitted = len(text) - limit
    logger.debug(
        "Smart truncate: %d → %d chars (head=%d body=%d tail=%d omitted=%d)",
        len(text), limit, head_n, body_n, tail_n, omitted,
    )

    parts = [
        head,
        f"\n\n[… {omitted:,} chars omitted …]\n\n",
        body,
    ]
    if tail:
        parts += [f"\n\n[… end of document …]\n\n", tail]

    return "".join(parts)


# ═══════════════════════════════════════════════════════════════════════════════
# Main Sensor Class
# ═══════════════════════════════════════════════════════════════════════════════

class SemanticVisionSensor:
    """
    Subscribes to `active_window_changed` events from WindowTracker.
    Extracts structured text using UIAutomation.
    Publishes `context_updated` on success, `semantic_extraction_failed` on failure.

    Threading model:
      Single background worker thread — serialises all UIA calls.
      Event-driven via threading.Event — zero idle CPU.
    """

    def __init__(self, bus, rate_limit_secs: float = RATE_LIMIT_SECS):
        self._bus            = bus
        self._rate_limit     = rate_limit_secs
        self._last_hash      = ""
        self._last_extract_t = 0.0
        self._pending        = threading.Event()
        self._lock           = threading.Lock()
        self._current_info:  Optional[dict] = None
        self._is_running     = False
        self._force_next      = False
        self._is_paused      = False
        self._thread:        Optional[threading.Thread] = None

        # Speed up tree searches (default is 8s — way too slow for real-time use)
        auto.uiautomation.SetGlobalSearchTimeout(UIA_TIMEOUT)

        self._bus.subscribe("active_window_changed", self._on_window_changed)
        self._bus.subscribe("request_vision_refresh", self._on_force_refresh)
        self._bus.subscribe("b_thinking", self._on_brain_busy)
        self._bus.subscribe("b_finished_thinking", self._on_brain_idle)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._is_running:
            return
        self._is_running = True
        self._thread = threading.Thread(
            target=self._worker_loop,
            daemon=True,
            name="SemanticVision.Worker",
        )
        self._thread.start()
        logger.info(
            "SemanticVisionSensor started | rate_limit=%.1fs | uia_timeout=%.1fs",
            self._rate_limit, UIA_TIMEOUT,
        )

    def stop(self) -> None:
        self._is_running = False
        self._pending.set()   # Wake the worker so it can exit
        logger.info("SemanticVisionSensor stop requested")

    # ── Event Handlers ────────────────────────────────────────────────────────

    def _on_window_changed(self, payload: dict) -> None:
        with self._lock:
            self._current_info = payload
        logger.debug(
            "Queued extraction | [%s] %r",
            payload.get("app_type", "?"), payload.get("title", "?"),
        )
        self._force_next = True  # Force immediate extraction for new window
        self._pending.set()

    def _on_force_refresh(self, payload: dict) -> None:
        logger.debug("Force refresh received via bus event")
        self._force_next = True
        self._pending.set()

    def _on_brain_busy(self, payload: dict) -> None:
        logger.debug("Brain busy — pausing semantic vision")
        self._is_paused = True

    def _on_brain_idle(self, payload: dict) -> None:
        logger.debug("Brain idle — resuming semantic vision")
        self._is_paused = False
        self._pending.set()
    def _worker_loop(self) -> None:
        logger.info("Extraction worker loop started")
        while self._is_running:
            # Block until a window-changed event arrives (or periodic wake-up)
            self._pending.wait(timeout=10.0)
            self._pending.clear()

            if not self._is_running:
                break
            
            if self._is_paused:
                self._pending.wait(timeout=5.0)
                continue

            # Rate-limit: don't hammer UIA unless forced
            since_last = time.monotonic() - self._last_extract_t
            if not self._force_next and since_last < self._rate_limit:
                sleep_for = self._rate_limit - since_last
                logger.debug("Rate limit: sleeping %.2fs", sleep_for)
                time.sleep(sleep_for)
            
            self._force_next = False # Reset flag

            with self._lock:
                info = self._current_info

            if not info:
                continue

            try:
                self._run_extraction(info)
            except Exception as exc:
                logger.error("Unhandled extraction exception: %s", exc, exc_info=True)

        logger.info("Extraction worker loop exiting")

    # ── Extraction Pipeline ───────────────────────────────────────────────────

    def _run_extraction(self, window_info: dict) -> None:
        """
        Full 7-step extraction pipeline. Every step is timed and logged.
        """
        t0       = time.monotonic()
        app_type = window_info.get("app_type", "unknown")
        title    = window_info.get("title", "?")
        hwnd     = window_info.get("hwnd", 0)
        process  = window_info.get("process_name", "?")

        # --- Safety: Don't analyze B's own window ---
        t_low = title.lower()
        if process.lower() in ["antigravity.exe", "python.exe"] or t_low == "b" or t_low.startswith("b -"):
             logger.debug("Skipping extraction: target is B's own window (title/process).")
             return

        logger.info(
            "╔══ EXTRACTION BEGIN ══ [%-15s] %r  (proc=%s hwnd=0x%X)",
            app_type.upper(), title[:60], process, hwnd,
        )

        # ── Step 1: Acquire UIA handle ────────────────────────────────────────
        logger.debug("Step 1  Acquiring UIA control for hwnd=0x%X", hwnd)
        t_uia = time.monotonic()
        try:
            window_ctrl = auto.ControlFromHandle(hwnd)
            if not window_ctrl or not window_ctrl.Exists(0, 0):
                logger.warning("Step 1  UIA control unavailable for hwnd=0x%X", hwnd)
                self._emit_failure(window_info, "uia_control_unavailable")
                return
            logger.debug("Step 1  UIA OK  [%.1f ms]", (time.monotonic() - t_uia) * 1000)
        except Exception as exc:
            logger.error("Step 1  ControlFromHandle(0x%X) raised: %s", hwnd, exc)
            self._emit_failure(window_info, f"uia_exception:{exc}")
            return

        # ── Step 2: Primary strategy ──────────────────────────────────────────
        strategy_fn = _STRATEGY_MAP.get(app_type, _strategy_generic)
        logger.info("Step 2  Strategy → %s", strategy_fn.__name__)
        t_primary = time.monotonic()
        try:
            result = strategy_fn(window_ctrl)
        except Exception as exc:
            logger.error("Step 2  Strategy %s raised: %s", strategy_fn.__name__, exc, exc_info=True)
            result = ExtractionResult("", "strategy_exception", 0.0, 0.0, {"error": str(exc)})
        primary_ms = (time.monotonic() - t_primary) * 1000
        logger.info("Step 2  %s  [%.1f ms]", result, primary_ms)

        # ── Step 3: Generic fallback if primary was weak ──────────────────────
        if not result.is_good and strategy_fn is not _strategy_generic:
            logger.info(
                "Step 3  Primary weak (q=%.2f len=%d) — running generic fallback",
                result.quality, len(result.text),
            )
            t_fallback = time.monotonic()
            try:
                fallback = _strategy_generic(window_ctrl)
            except Exception as exc:
                logger.error("Step 3  Generic fallback raised: %s", exc, exc_info=True)
                fallback = _EMPTY
            fallback_ms = (time.monotonic() - t_fallback) * 1000
            logger.info("Step 3  Fallback: %s  [%.1f ms]", fallback, fallback_ms)

            if fallback.quality > result.quality:
                logger.info(
                    "Step 3  Accepting fallback (%.2f > %.2f)", fallback.quality, result.quality
                )
                result = fallback
            else:
                logger.info(
                    "Step 3  Keeping primary (%.2f ≥ %.2f)", result.quality, fallback.quality
                )
        else:
            logger.debug("Step 3  Skipped (primary good, or already using generic)")

        # ── Step 4: Quality gate ──────────────────────────────────────────────
        if not result.is_good:
            logger.warning(
                "╚══ EXTRACTION FAIL ══ q=%.2f len=%d — emitting semantic_extraction_failed",
                result.quality, len(result.text),
            )
            self._emit_failure(window_info, "quality_gate_failed")
            return

        # ── Step 5: Smart truncation ──────────────────────────────────────────
        snippet = _smart_truncate(result.text, SNIPPET_MAX_CHARS)
        logger.debug(
            "Step 5  Truncate: %d → %d chars", len(result.text), len(snippet)
        )

        # ── Step 6: MD5 deduplication ─────────────────────────────────────────
        content_hash = hashlib.md5(
            f"{title}||{snippet}".encode("utf-8", errors="replace")
        ).hexdigest()
        if content_hash == self._last_hash:
            logger.debug(
                "Step 6  Content unchanged (hash=%s…) — skipping publish", content_hash[:8]
            )
            return
        self._last_hash      = content_hash
        self._last_extract_t = time.monotonic()

        # ── Step 7: Publish ───────────────────────────────────────────────────
        total_ms = (time.monotonic() - t0) * 1000
        payload = {
            "window_title":      title,
            "screen_text":       snippet,
            "app_type":          app_type,
            "extraction_source": result.source,
            "quality_score":     round(result.quality, 3),
            "content_length":    len(result.text),
            "snippet_length":    len(snippet),
            "extraction_ms":     round(total_ms, 1),
            **result.metadata,
        }
        self._bus.publish("context_updated", payload)
        logger.info(
            "╚══ EXTRACTION OK  ══ source=%-32s q=%.2f  %d→%d chars  [%.1f ms]",
            result.source, result.quality,
            len(result.text), len(snippet), total_ms,
        )

    def _emit_failure(self, window_info: dict, reason: str) -> None:
        """Signal downstream (OCR fallback) that UIA extraction failed."""
        self._bus.publish("semantic_extraction_failed", {
            **window_info,
            "failure_reason": reason,
        })
        logger.debug("Emitted semantic_extraction_failed | reason=%s", reason)