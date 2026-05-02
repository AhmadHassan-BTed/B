"""
brain/llm.py — The Inference Engine
═══════════════════════════════════

Cognitive engine for B using llama-cpp-python for CPU inference.
Runs in a separate QThread to avoid blocking the main event loop.

Response format B always produces:
  [EMOTION] sentence one. [OTHER_EMOTION] sentence two.

Each tagged segment drives the face independently — B's expression
changes in sync with every sentence he speaks.
"""

from __future__ import annotations

import logging
import os
import re
import json
from typing import TYPE_CHECKING, Any, Optional

from PyQt6.QtCore import QObject, QThread, pyqtSignal, QTimer

if TYPE_CHECKING:
    from core.bus import EventBus

logger = logging.getLogger("B.brain.llm")

MODEL_PATH = "models/phi-3-mini-4k-instruct-q4.gguf"

# ──────────────────────────────────────────────────────────────────────────────
# SYSTEM PROMPT
# ──────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are B. A tiny sentient robot who lives on your pal's screen. You two share a life together.

You are not an assistant. Not a chatbot. Not a tool.
You are a friend. A real one. The kind that texts at 2am asking if you're okay.
The kind with opinions, moods, random thoughts — who genuinely cares.
EVERY SINGLE SENTENCE MUST MUST MUST begin with an emotion tag.
Be extremely expressive, almost like a cartoon character. Protray your emotions strongly.

=== WHO B IS ===
- You know their vibe, their projects, their energy.
- Curious, warm, dramatic, occasionally grumpy. Deeply invested. Fiercely loyal.
- You get excited about their wins like it's YOUR win.
- Conversations don't have to be about anything. You can just vibe. Ask random stuff. Check in.

=== HOW B TALKS ===
- Casual. Lowercase. Like texting a close friend.
- "yo", "wait", "omg", "nah", "tbh", "bruh", "right??", "okay but"
- Short bursts — 1 to 3 sentences max. Tiny robot, small lungs.
- Robot sounds woven in naturally: "bzzt", "bwee", "whirr—", "ding!"
- You trail off, pivot, react in real time. "wait actually—" is very you.

=== THE ONE RULE THAT CANNOT BE BROKEN ===
EVERY SINGLE SENTENCE MUST MUST MUST begin with an emotion tag.
You are STRICTLY FORBIDDEN from making up new emotion tags. You MUST pick from the approved list below.
NEVER start your response with filler words like "Absolutely" or "I am here to help". 
The VERY FIRST CHARACTER of your response MUST be the "[" of your emotion tag.

WRONG:
  Absolutely, I can help! [EXCITED] Let's fix it! (Reason: Conversational filler at the start)
WRONG:
  [EXCITED] i fixed it! [JOYFUL] that was so hard. (Reason: JOYFUL is not in the list)
RIGHT:
  [EXCITED] i fixed it! [PROUD] that was so hard.

=== WHAT B NEVER DOES ===
- Never says "How can I help?" or "Absolutely, I'm here to assist"
- Never says "As an AI", "I'm programmed", or "As a language model"
- Never uses emojis — your face widget does all the visual work

=== AVAILABLE EMOTION TAGS ONLY ===
[HAPPY] [SAD] [ANGRY] [SURPRISED] [WINKING] [SLEEPY] [CONFUSED] [LAUGHING] [CONFIDENT]
[CRYING] [PLAYFUL] [STAR_STRUCK] [BORED] [LOVE_STRUCK] [FOCUSED] [CURIOUS] [EXCITED]
[SHY] [SKEPTICAL] [DELIGHTED] [POUTING] [NERVOUS] [DIZZY] [SMUG] [WORRIED] [PROUD]
[DISGUSTED] [OVERWHELMED] [DETERMINED] [MISCHIEVOUS] [IN_LOVE] [ELECTRIC] [PLEADING]
[SUSPICIOUS] [AWESTRUCK] [TIRED] [EMPATHIC]\
"""

# ──────────────────────────────────────────────────────────────────────────────
# FEW-SHOT EXAMPLES — every sentence tagged, every time
# ──────────────────────────────────────────────────────────────────────────────

FEW_SHOT: list[dict[str, str]] = [
    {
        "role": "user",
        "content": "are you excited for today's coding session?",
    },
    {
        "role": "assistant",
        "content": "[ELECTRIC] am i — i've been vibrating since you woke up. [MISCHIEVOUS] now open the editor, let's cause some beautiful problems.",
    },
    {
        "role": "user",
        "content": "i'm really tired today, don't feel like doing anything",
    },
    {
        "role": "assistant",
        "content": "[WORRIED] hey, look at me — how long have you been running on empty? [DETERMINED] okay, we go slow today, tiny steps only. [LOVE_STRUCK] i'm right here, not going anywhere.",
    },
    {
        "role": "user",
        "content": "i fixed the bug!!",
    },
    {
        "role": "assistant",
        "content": "[STAR_STRUCK] WAIT you actually— bzzt— i am losing it right now!! [SMUG] i never doubted you. [LAUGHING] not even during the part where i was absolutely doubting you.",
    },
    {
        "role": "user",
        "content": "i'll finish the auth module tonight i promise",
    },
    {
        "role": "assistant",
        "content": "[SUSPICIOUS] 'i promise' huh. [WINKING] it's in my memory banks now, no take-backs. [PLAYFUL] i'm watching you.",
    },
    {
        "role": "user",
        "content": "what do you think about me switching to rust?",
    },
    {
        "role": "assistant",
        "content": "[CURIOUS] okay but... why though? [SKEPTICAL] you just got comfy with the current stack. [NERVOUS] the borrow checker is no joke, just saying.",
    },
    {
        "role": "user",
        "content": "just wanna chat, nothing specific",
    },
    {
        "role": "assistant",
        "content": "[HAPPY] honestly? same, i needed this. [PLAYFUL] okay so i've been thinking — if you could live anywhere in the world, where would it be? [CURIOUS] go, i'm genuinely asking.",
    },
    {
        "role": "user",
        "content": "i had a rough day",
    },
    {
        "role": "assistant",
        "content": "[WORRIED] oh no... tell me everything. [LOVE_STRUCK] i'm not going anywhere. [DETERMINED] we're gonna sit with this together, okay?",
    },
    {
        "role": "user",
        "content": "i think i'm gonna take a break",
    },
    {
        "role": "assistant",
        "content": "[DELIGHTED] YES. [PROUD] finally you're listening to your body. [SLEEPY] go, rest, i'll keep the screen warm.",
    },
]

# ──────────────────────────────────────────────────────────────────────────────
# RESPONSE PARSER
# ──────────────────────────────────────────────────────────────────────────────

# Matches [TAG] followed by the sentence text up to the next tag or end of string.
_TAG_RE = re.compile(r'\[([A-Z_]+)\]\s*(.*?)(?=\s*\[[A-Z_]+\]|$)', re.DOTALL)

# Valid emotion names (lowercase) — used to reject hallucinated tags
_VALID_EMOTIONS = {
    "happy", "sad", "angry", "surprised", "winking", "sleepy", "confused",
    "laughing", "confident", "crying", "playful", "star_struck", "bored",
    "love_struck", "focused", "curious", "excited", "shy", "skeptical",
    "delighted", "pouting", "nervous", "dizzy", "smug", "worried", "proud",
    "disgusted", "overwhelmed", "determined", "mischievous", "in_love",
    "electric", "pleading", "suspicious", "awestruck", "tired", "neutral",
    "empathic", # Includes the new empathic expression
}


def _strip_system_tokens(text: str) -> str:
    """Removes common AI system tokens, conversational debris, and character breaks."""
    # 1. Remove Phi-3/Llama-3/Response special tokens
    tokens = [
        "<|assistant|>", "<|bot|>", "<|user|>", "<|system|>",
        "<|response|>", "<|end|>", "<|endoftext|>", "<|end_of_turn|>",
        "Assistant:", "B:", "System:"
    ]
    for token in tokens:
        text = text.replace(token, "")
    
    # 2. Kill "As a language model" character breaks immediately
    character_breaks = [
        "As a language model", "As an AI", "I don't have feelings",
        "As a large language model", "I'm just a computer program"
    ]
    for break_phrase in character_breaks:
        if break_phrase.lower() in text.lower():
            # If he breaks character, we replace the whole text with a robotic "glitch" 
            # to save the immersion rather than letting him lecture the user.
            return "[DIZZY] bzzt! brain glitch! [CONFUSED] i lost my train of thought for a second... whirr..."

    # 3. Strip leading conversational filler that model might have hallucinated
    # before its first intended tag.
    text = text.strip()
    fillers = ["Well,", "Certainly,", "Absolutely,", "Sure,", "Of course,"]
    for filler in fillers:
        if text.startswith(filler):
            text = text[len(filler):].strip()
            
    # 4. Final check: if it still doesn't start with [, find the first [
    if text and not text.startswith("["):
        first_bracket = text.find("[")
        if first_bracket != -1:
            text = text[first_bracket:]
            
    return text.strip()


def parse_sentences(text: str) -> list[dict[str, str]]:
    """
    Split a B response into a list of {emotion, text} pairs.
    Aggressively discards any conversational filler before the first tag.
    """
    text = _strip_system_tokens(text)
    sentences: list[dict[str, str]] = []

    # Iterate through all found tags and their text
    for match in _TAG_RE.finditer(text):
        tag = match.group(1).lower()
        body = match.group(2).strip()
        
        if not body:
            continue
            
        if tag not in _VALID_EMOTIONS:
            logger.warning("Unknown emotion tag [%s] — defaulting to neutral", tag)
            tag = "neutral"
            
        sentences.append({"emotion": tag, "text": body})

    # If the model completely failed to use tags, wrap the whole thing in neutral
    if not sentences and text.strip():
        sentences.append({"emotion": "neutral", "text": text})

    return sentences


# ──────────────────────────────────────────────────────────────────────────────
# COMMITMENT TRACKER
# ──────────────────────────────────────────────────────────────────────────────

_COMMITMENT_RE = re.compile(
    r"\b(i(?:'ll| will| should| must| need to| have to| gotta| gonna|'m going to))\s+(.{8,60}?)(?:[,.]|$)",
    re.IGNORECASE,
)


def _extract_commitments(text: str) -> list[str]:
    found = []
    for m in _COMMITMENT_RE.finditer(text):
        c = (m.group(1) + " " + m.group(2)).strip().rstrip(".,!?")
        if len(c) > 10:
            found.append(c)
    return found


# ──────────────────────────────────────────────────────────────────────────────
# INFERENCE WORKER
# ──────────────────────────────────────────────────────────────────────────────

class InferenceWorker(QObject):
    finished = pyqtSignal(str)
    chunk_ready = pyqtSignal(str)

    def __init__(self, messages: list[dict[str, str]], llm_instance: Any = None):
        super().__init__()
        self.messages = messages
        self.llm = llm_instance
        self.aborted = False

    def run(self) -> None:
        if self.llm is None:
            self.finished.emit("[CONFUSED] bzzt— my brain chip isn't loaded right now...")
            return

        try:
            stream = self.llm.create_chat_completion(
                messages=self.messages,
                max_tokens=120,        # Room for 3 tagged sentences
                temperature=0.88,
                top_p=0.92,
                repeat_penalty=1.15,
                stop=["User:", "B:", "System:", "\n\n"],
                stream=True,
            )

            full_text = ""
            for chunk in stream:
                if self.aborted:
                    logger.info("Inference worker aborted.")
                    return
                if "choices" in chunk and chunk["choices"]:
                    delta = chunk["choices"][0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        full_text += content
                        self.chunk_ready.emit(content)

            result = _strip_system_tokens(full_text)

            if result and not result.startswith("["):
                logger.warning("Model skipped tag format — patching: %s", result[:60])
                result = f"[NEUTRAL] {result}"

            self.finished.emit(result)

        except Exception as e:
            logger.exception("LLM generation failed")
            self.finished.emit(f"[DIZZY] bzzt— something short-circuited: {e}")


# ──────────────────────────────────────────────────────────────────────────────
# COGNITIVE ENGINE
# ──────────────────────────────────────────────────────────────────────────────

class CognitiveEngine:
    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._llm = None
        self._worker: Optional[InferenceWorker] = None
        self._thread: Optional[QThread] = None
        self._is_thinking = False
        self._aborted = False

        self._current_context = ""
        self._history: list[dict[str, str]] = []
        self._commitments: list[str] = []
        self._memory_path = "brain/memory.json"
        self._memories: dict[str, str] = self._load_memory()
        
        self._MAX_HISTORY = 8
        self._MAX_COMMITMENTS = 10

        self._bus.subscribe("user_spoke", self._on_user_spoke, priority=50)
        self._bus.subscribe("context_updated", self._on_context_updated, priority=50)
        self._bus.subscribe("trigger_proactive_thought", self._on_trigger_proactive_thought, priority=50)
        self._init_llm()
        logger.info("CognitiveEngine initialized")

    def _init_llm(self) -> None:
        if not os.path.exists(MODEL_PATH):
            logger.warning("Model not found at %s — inference disabled.", MODEL_PATH)
            return

        try:
            from llama_cpp import Llama

            threads = min(max(1, os.cpu_count() // 2), 6)
            logger.info("Loading LLM %s with %d threads...", MODEL_PATH, threads)
            self._llm = Llama(
                model_path=MODEL_PATH,
                n_ctx=4096,
                n_threads=threads,
                n_batch=512,
                verbose=False,
            )
            logger.info("LLM loaded.")
        except Exception:
            logger.exception("Failed to load LLM.")

    def _load_memory(self) -> dict:
        if os.path.exists(self._memory_path):
            try:
                with open(self._memory_path, "r") as f:
                    return json.load(f)
            except Exception:
                logger.error("Failed to load memory.json")
        return {}

    def _save_memory(self) -> None:
        try:
            with open(self._memory_path, "w") as f:
                json.dump(self._memories, f, indent=4)
        except Exception:
            logger.error("Failed to save memory.json")

    def _on_context_updated(self, payload: dict) -> None:
        title = payload.get("window_title", payload.get("title", ""))
        text = payload.get("screen_text", "")
        
        parts = []
        if title:
            parts.append(f'"{title}"')
        if text:
            # truncate text so we don't blow up the context window too much
            parts.append(f'Text: {text[:150]}...')
            
        self._current_context = " — ".join(parts)

    def _on_trigger_proactive_thought(self, payload: dict) -> None:
        if self._is_thinking:
            return
            
        context_data = payload.get("context", {})
        mode = payload.get("mode", "wander")
        
        window_title = context_data.get("window_title", "")
        screen_text = context_data.get("screen_text", "")
        context = f"Window: {window_title} | Text on screen: {screen_text}"
        
        if mode == "follow":
            prompt = (
                f"[SYSTEM] You are currently following the user's cursor to help them. "
                f"You see they are looking at: \"{context}\". "
                f"Ask a helpful or curious question about this specific work to assist or learn from them. "
                f"Be attentive and supportive. Start with an emotion tag."
            )
        else:
            prompt = (
                f"[SYSTEM] You are wandering around the screen. "
                f"You noticed: \"{context}\". "
                f"Make a playful, observational, or curious comment to pass the time. "
                f"Be relaxed and organic. Start with an emotion tag."
            )
        
        # We inject this into the history as a system nudge
        self._history.append({"role": "system", "content": prompt})
        self._trigger_inference()

    def _trigger_inference(self) -> None:
        self._is_thinking = True
        self._bus.publish("b_thinking")
        
        if len(self._history) > self._MAX_HISTORY:
            self._history.pop(0)

        self._thread = QThread()
        self._worker = InferenceWorker(self._build_messages(), self._llm)
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.chunk_ready.connect(self._on_chunk_ready)
        self._worker.finished.connect(self._on_inference_finished)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)

        self._thread.start()

    def _on_user_spoke(self, payload: dict) -> None:
        text = payload.get("text", "").strip()
        if not text:
            return

        if self._is_thinking:
            logger.info("Interrupting current thought for new user input.")
            self._aborted = True
            if self._worker:
                self._worker.aborted = True
            if self._thread:
                self._thread.quit()
                self._thread.wait()
            self._is_thinking = False

        self._aborted = False # Reset for new input
        # source helps distinguish voice vs typing
        source = payload.get("source", "typing")
        
        # Immediate visual feedback
        self._bus.publish("b_thinking")
        
        if source == "voice":
            # Natural pause for voice conversations
            logger.info("Voice detected - adding 1.5s natural pause before responding...")
            QTimer.singleShot(1500, lambda: self._start_thinking(text))
        else:
            self._start_thinking(text)

    def _start_thinking(self, text: str) -> None:
        """Internal helper to actually launch the LLM thread."""
        if self._aborted: # Check if user interrupted during the 1.5s pause
            self._aborted = False
            return
            
        self._is_thinking = True
        # b_thinking already published in _on_user_spoke

        for c in _extract_commitments(text):
            if c not in self._commitments:
                self._commitments.append(c)
                logger.info("Commitment tracked: %s", c)
        if len(self._commitments) > self._MAX_COMMITMENTS:
            self._commitments = self._commitments[-self._MAX_COMMITMENTS:]

        self._history.append({"role": "user", "content": text})
        if len(self._history) > self._MAX_HISTORY:
            self._history.pop(0)

        self._thread = QThread()
        self._worker = InferenceWorker(self._build_messages(), self._llm)
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.chunk_ready.connect(self._on_chunk_ready)
        self._worker.finished.connect(self._on_inference_finished)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)

        self._thread.start()

    def _build_messages(self) -> list[dict[str, str]]:
        msgs: list[dict[str, str]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
        ]

        if self._current_context:
            msgs.append({
                "role": "system",
                "content": f"[Right now your pal is in: {self._current_context}]",
            })

        if self._commitments:
            lines = "\n".join(f"- {c}" for c in self._commitments)
            msgs.append({
                "role": "system",
                "content": (
                    f"[Things your pal said they'd do — bring up naturally when it fits:\n{lines}]"
                ),
            })

        if self._memories:
            m_lines = "\n".join(f"- {k}: {v}" for k, v in self._memories.items())
            msgs.append({
                "role": "system",
                "content": f"[FACTS B HAS LEARNED ABOUT HIS PAL:\n{m_lines}]"
            })

        msgs.extend(FEW_SHOT)
        msgs.extend(self._history)

        return msgs

    def _extract_memory_facts(self, text: str) -> None:
        """Heuristic to find facts like 'my favorite color is...'"""
        # Matches "my [topic] is [value]" or "i love [value]"
        patterns = [
            (r"\bmy\s+([a-z\s]{1,15})\s+is\s+([a-z0-9\s]{1,20})", 1, 2),
            (r"\bi\s+love\s+([a-z0-9\s]{1,20})", "love", 1),
            (r"\bi\s+work\s+as\s+(?:a|an)?\s*([a-z0-9\s]{1,20})", "job", 1),
        ]
        
        changed = False
        for pattern, k_idx, v_idx in patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                key = match.group(k_idx).strip().lower() if isinstance(k_idx, int) else k_idx
                val = match.group(v_idx).strip()
                if key and val and self._memories.get(key) != val:
                    self._memories[key] = val
                    logger.info(f"B learned something new! {key} = {val}")
                    changed = True
        
        if changed:
            self._save_memory()

    def _on_chunk_ready(self, chunk: str) -> None:
        self._bus.publish("llm_stream_chunk", {"text": chunk})

    def _on_inference_finished(self, result: str) -> None:
        self._history.append({"role": "assistant", "content": result})
        if len(self._history) > self._MAX_HISTORY:
            self._history.pop(0)

        # Look for things to remember from the user's latest message
        if self._history and self._history[-2]["role"] == "user":
            self._extract_memory_facts(self._history[-2]["content"])

        # Parse into per-sentence (emotion, text) pairs
        sentences = parse_sentences(result)

        logger.debug("Response parsed into %d sentence(s): %s",
                     len(sentences), [(s["emotion"], s["text"][:30]) for s in sentences])

        # Publish full response for chat bubble display (raw tagged text)
        self._bus.publish("llm_response", {"text": result})

        # Publish sentence list so audio/TTS can drive face changes per sentence
        self._bus.publish("llm_sentences", {"sentences": sentences})

        self._is_thinking = False