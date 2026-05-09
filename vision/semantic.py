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
BLACKLIST_THRESHOLD = 3      # Consecutive failures before bypassing UIA for a process
INITIAL_COOLDOWN    = 60.0   # Initial bypass duration (seconds)
MAX_COOLDOWN        = 300.0  # Max backoff duration (5 minutes)

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
    spatial_map: dict = field(default_factory=dict) # NEW: Maps ID strings to (x, y) center coords

    @property
    def is_good(self) -> bool:
        return len(self.text.strip()) >= MIN_QUALITY_CHARS and self.quality > 0.1

    def __str__(self) -> str:
        return (
            f"ExtractionResult("
            f"source={self.source!r}, q={self.quality:.2f}, "
            f"len={len(self.text)}, t={self.elapsed_ms:.1f}ms, spatial_nodes={len(self.spatial_map)})"
        )


class SpatialMapManager:
    """
    Tracks UI elements and assigns them short integer IDs during an extraction cycle.
    Stores (x, y, label) tuples so the LLM can match user requests to spatial IDs.
    Resets every extraction cycle.
    """
    def __init__(self):
        self._map = {}  # ID (str) -> (x, y, label)
        self._next_id = 1

    def register(self, ctrl) -> Optional[int]:
        """Fetches coordinates, label, and control type, assigns an ID, and returns it."""
        try:
            rect = ctrl.BoundingRectangle
            if rect:
                w = rect.right - rect.left
                h = rect.bottom - rect.top
                
                if w > 0 and h > 0:
                    center_x = rect.left + (w // 2)
                    center_y = rect.top + (h // 2)
                    
                    # Check if it's actually on a reasonable screen area
                    if -10000 < center_x < 10000 and -10000 < center_y < 10000:
                        # Capture the accessible name as a human-readable label
                        label = ""
                        try:
                            label = (ctrl.Name or "").strip()[:60]
                        except Exception:
                            pass
                        
                        # Extract Control Type for semantic context
                        ctype_name = "UNKNOWN"
                        try:
                            ctype_name = ctrl.ControlTypeName.replace("Control", "").upper()
                        except Exception:
                            pass

                        node_id = self._next_id
                        self._map[str(node_id)] = {
                            "coords": (center_x, center_y),
                            "label": label,
                            "type": ctype_name
                        }
                        self._next_id += 1
                        logger.debug(
                            "SpatialMap: Registered [#%d] [%s] '%s' at (%d, %d)",
                            node_id, ctype_name, label[:20], center_x, center_y,
                        )
                        return node_id
        except Exception as e:
            logger.debug("SpatialMap: Registration failed: %s", e)
        return None

    def get_map(self) -> Dict[str, Tuple]:
        return self._map


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


def _read_control(ctrl, spatial_manager: Optional[SpatialMapManager] = None) -> str:
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
    
    text = text.strip()
    if not text:
        return ""

    # Spatial Mapping: Assign an ID and prepend semantic tag + ID to the text
    if spatial_manager:
        node_id = spatial_manager.register(ctrl)
        if node_id:
            ctype_name = "TEXT"
            try:
                ctype_name = ctrl.ControlTypeName.replace("Control", "").upper()
            except Exception: pass
            return f"[#{node_id}] [{ctype_name}] {text}"

    return text


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

def _strategy_browser(window, spatial_manager: Optional[SpatialMapManager] = None) -> ExtractionResult:
    """
    Chromium / Firefox extraction.
    """
    t = time.monotonic()
    metadata: dict = {}
    
    # ── URL ──────────────────────────────────────────────────────────────────
    try:
        addr = window.EditControl(searchDepth=6)
        if addr.Exists(0, 0):
            url = _try_value_pattern(addr)
            if url and ("://" in url or url.startswith("about:")):
                metadata["url"] = url
    except Exception: pass

    # ── Page content ──────────────────────────────────────────────────────────
    content_blocks = []
    logger.debug("[browser] Searching for DocumentControl…")
    try:
        # Search for all documents and pick the biggest one
        documents = []
        for d, _ in auto.WalkControl(window, maxDepth=12):
            if d.ControlType == _CT.DocumentControl:
                documents.append(d)
        
        if not documents:
            # Fallback to PaneControl if it's an Electron app that didn't tag its doc
            for p, _ in auto.WalkControl(window, maxDepth=6):
                if p.ControlType == _CT.PaneControl and p.Name:
                    documents.append(p)

        best_doc = None
        best_text = ""
        
        for doc in documents:
            text = _try_text_pattern(doc)
            if not text:
                # Fallback: walk the doc for text if TextPattern failed
                text_parts = []
                for child, _ in auto.WalkControl(doc, maxDepth=3):
                    if child.ControlType in [_CT.TextControl, _CT.EditControl, _CT.LinkControl]:
                        val = child.Name or _try_value_pattern(child)
                        if val: text_parts.append(val)
                text = " ".join(text_parts)
            
            if len(text) > len(best_text):
                best_text = text
                best_doc = doc

        if best_doc:
            main_id = spatial_manager.register(best_doc) if spatial_manager else None
            prefix = ""
            if main_id:
                ctype = "DOCUMENT"
                try: ctype = best_doc.ControlTypeName.replace("Control", "").upper()
                except Exception: pass
                prefix = f"[#{main_id}] [{ctype}] "
            
            content_blocks.append(f"{prefix}{best_text}")
            
            # Spatial IDs for specific items (Links, etc.)
            if spatial_manager:
                for child, _ in auto.WalkControl(best_doc, maxDepth=5):
                    ctype = child.ControlType
                    if ctype in [_CT.ListItemControl, _CT.LinkControl, _CT.ButtonControl]:
                        name = child.Name.strip()
                        if name and len(name) > 2:
                            node_id = spatial_manager.register(child)
                            if node_id:
                                ctype_tag = ctype.replace("Control", "").upper()
                                content_blocks.append(f"[#{node_id}] [{ctype_tag}] {name}")

    except Exception as e:
        logger.debug("[browser] Strategy error: %s", e)

    final_content = "\n".join(content_blocks)
    elapsed = (time.monotonic() - t) * 1000
    return ExtractionResult(
        text=final_content, source="browser_document",
        quality=_score(final_content), elapsed_ms=elapsed, metadata=metadata,
        spatial_map=spatial_manager.get_map() if spatial_manager else {}
    )


def _strategy_code_editor(window, spatial_manager: Optional[SpatialMapManager] = None) -> ExtractionResult:
    """
    VS Code / Cursor / Notepad++ / Sublime / JetBrains extraction.
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
    except Exception:
        pass

    # ── Try EditControl ───────────────────────────────────────────────────────
    try:
        edit = window.EditControl(searchDepth=6)
        if edit.Exists(0, 0):
            content = _read_control(edit, spatial_manager)
    except Exception as e:
        logger.debug("[code_editor] EditControl failed: %s", e)

    # ── Try DocumentControl if EditControl came up short ─────────────────────
    if len(content) < MIN_QUALITY_CHARS:
        try:
            doc = window.DocumentControl(searchDepth=6)
            if doc.Exists(0, 0):
                doc_text = _read_control(doc, spatial_manager)
                if len(doc_text) > len(content):
                    content = doc_text
        except Exception as e:
            logger.debug("[code_editor] DocumentControl failed: %s", e)

    elapsed = (time.monotonic() - t) * 1000
    return ExtractionResult(
        text=content, source="code_editor_buffer",
        quality=_score(content), elapsed_ms=elapsed, metadata=metadata,
        spatial_map=spatial_manager.get_map() if spatial_manager else {}
    )


def _strategy_terminal(window, spatial_manager: Optional[SpatialMapManager] = None) -> ExtractionResult:
    """
    Windows Terminal / CMD / PowerShell extraction.
    """
    t = time.monotonic()
    content = ""
    full_len = 0
    source_name = "terminal_tail"

    for label, finder in [
        ("DocumentControl", lambda: window.DocumentControl(searchDepth=5)),
        ("EditControl",     lambda: window.EditControl(searchDepth=5)),
    ]:
        try:
            ctrl = finder()
            if ctrl.Exists(0, 0):
                raw = _read_control(ctrl, spatial_manager)
                full_len = len(raw)
                # Take the tail — that's where the latest prompt + output is
                content = raw[-2_500:] if full_len > 2_500 else raw
                source_name = f"terminal_tail:{label}"
                break
        except Exception as e:
            logger.debug("[terminal] %s failed: %s", label, e)

    elapsed = (time.monotonic() - t) * 1000
    return ExtractionResult(
        text=content, source=source_name,
        quality=_score(content), elapsed_ms=elapsed,
        metadata={"full_buffer_length": full_len},
        spatial_map=spatial_manager.get_map() if spatial_manager else {}
    )


def _strategy_office(window, spatial_manager: Optional[SpatialMapManager] = None) -> ExtractionResult:
    """
    Microsoft Office extraction.
    """
    t = time.monotonic()
    content = ""

    try:
        doc = window.DocumentControl(searchDepth=5)
        if doc.Exists(0, 0):
            content = _read_control(doc, spatial_manager)
    except Exception as e:
        logger.debug("[office] DocumentControl failed: %s", e)

    elapsed = (time.monotonic() - t) * 1000
    return ExtractionResult(
        text=content, source="office_document",
        quality=_score(content), elapsed_ms=elapsed, metadata={},
        spatial_map=spatial_manager.get_map() if spatial_manager else {}
    )


def _strategy_generic(window, spatial_manager: Optional[SpatialMapManager] = None) -> ExtractionResult:
    """
    Universal fallback tree-walk.
    """
    t = time.monotonic()
    candidates: List[Tuple[float, str, str, int]] = []   # (score, text, type_name, depth)
    nodes_visited = 0

    try:
        for ctrl, depth in auto.WalkControl(window, maxDepth=TREE_MAX_DEPTH):
            nodes_visited += 1
            if nodes_visited > TREE_MAX_NODES: break

            ctype = ctrl.ControlType
            if ctype in SKIP_TYPES or ctype not in CONTENT_TYPES:
                continue

            text = _read_control(ctrl, spatial_manager)
            if not text or len(text.strip()) < MIN_QUALITY_CHARS:
                continue

            s = _score(text)
            candidates.append((s, text, ctrl.ControlTypeName, depth))
            if s > 0.85: break

    except Exception as e:
        logger.debug("[generic] Tree walk exception: %s", e)

    elapsed = (time.monotonic() - t) * 1000
    if not candidates:
        return ExtractionResult("", "generic:empty", 0.0, elapsed, {})

    candidates.sort(key=lambda x: x[0], reverse=True)
    best_score, best_text, best_type, best_depth = candidates[0]

    return ExtractionResult(
        text=best_text,
        source=f"generic_tree_walk:{best_type}",
        quality=best_score,
        elapsed_ms=elapsed,
        metadata={
            "nodes_visited": nodes_visited,
            "winning_type": best_type,
        },
        spatial_map=spatial_manager.get_map() if spatial_manager else {}
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

        # ISSUE 2 REFACTOR: Smart Cooldown Tracker (process_name -> metadata dict)
        self._blacklist_metadata: Dict[str, dict] = {}

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

        # --- ISSUE 2: Smart Cooldown / Probe Check ---
        now = time.monotonic()
        meta = self._blacklist_metadata.get(process, {"failures": 0, "cooldown_until": 0.0, "next_cooldown": INITIAL_COOLDOWN})
        
        if meta["failures"] >= BLACKLIST_THRESHOLD:
            if now < meta["cooldown_until"]:
                logger.debug("Step 0  BYPASSING UIA: %s is on cooldown (%.1fs remaining)", process, meta["cooldown_until"] - now)
                self._emit_failure(window_info, "blacklisted_cooldown")
                return
            else:
                logger.info("Step 0  PROBING previously blacklisted process: %s", process)

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
        spatial_manager = SpatialMapManager()
        strategy_fn = _STRATEGY_MAP.get(app_type, _strategy_generic)
        logger.info("Step 2  Strategy → %s", strategy_fn.__name__)
        t_primary = time.monotonic()
        try:
            result = strategy_fn(window_ctrl, spatial_manager)
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
                fallback = _strategy_generic(window_ctrl, spatial_manager)
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
            
            # ISSUE 2: Update cooldown metadata on failure
            meta = self._blacklist_metadata.get(process, {"failures": 0, "cooldown_until": 0.0, "next_cooldown": INITIAL_COOLDOWN})
            meta["failures"] += 1
            if meta["failures"] >= BLACKLIST_THRESHOLD:
                duration = meta["next_cooldown"]
                meta["cooldown_until"] = time.monotonic() + duration
                meta["next_cooldown"] = min(MAX_COOLDOWN, duration * 2) # Exponential backoff
                logger.warning("Step 4  Process %s blacklisted for %.1fs (total failures: %d)", process, duration, meta["failures"])
            
            self._blacklist_metadata[process] = meta
            self._emit_failure(window_info, "quality_gate_failed")
            return

        # Success! Reset failure count and remove cooldown for this process
        if process in self._blacklist_metadata:
            logger.info("Step 4  Process %s RECOVERED — clearing blacklist metadata", process)
            del self._blacklist_metadata[process]

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
        self._last_spatial_map = result.spatial_map

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
            "spatial_map":       result.spatial_map,
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