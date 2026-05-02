"""
brain/soul.py — B's Inner Life & Real-Time Stream Buffer
════════════════════════════════════════════════════════

Manages B's physiological states (blinking, resting) AND parses the 
LLM stream in real-time. As B thinks, this module chops the incoming 
tokens into sentences, strips AI filler, updates the Chat Bubble, 
and queues the audio.
"""

from __future__ import annotations

import logging
import random
import re
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.bus import EventBus

logger = logging.getLogger("B.brain.soul")

# ──────────────────────────────────────────────────────────────────────
# Timing Constants
# ──────────────────────────────────────────────────────────────────────
BLINK_MIN = 3.0
BLINK_MAX = 6.0
BLINK_DURATION = 0.15
LINGER_DURATION = 4.0  # How long B holds his emotion after speaking

# Used to catch hallucinated tags on the fly
_VALID_EMOTIONS = {
    "happy", "sad", "angry", "surprised", "winking", "sleepy", "confused",
    "laughing", "confident", "crying", "playful", "star_struck", "bored",
    "love_struck", "focused", "curious", "excited", "shy", "skeptical",
    "delighted", "pouting", "nervous", "dizzy", "smug", "worried", "proud",
    "disgusted", "overwhelmed", "determined", "mischievous", "in_love",
    "electric", "pleading", "suspicious", "awestruck", "tired", "neutral",
    "empathic", "sleeping"
}

_EMOTION_ALIASES = {
    "frustrated": "angry",
    "irritated": "angry",
    "irate": "angry",
    "annoyed": "angry",
    "angry_voice": "angry",
    "furious": "angry",
    "loving": "love_struck",
    "affectionate": "love_struck",
    "sweet": "love_struck",
    "with_love": "love_struck",
    "puzzled": "confused",
    "questioning": "confused",
    "scared": "nervous",
    "panicked": "overwhelmed",
    "yawning": "tired",
    "moray": "neutral", # From user logs
}

def map_emotion(tag: str) -> str:
    """Maps a raw tag to the nearest valid emotion string."""
    tag = tag.lower().strip()
    if tag in _VALID_EMOTIONS:
        return tag
    return _EMOTION_ALIASES.get(tag, "neutral")

class StateMachine:
    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._behavior_mode: str = "wander"
        self._base_emotion: str = "sad" # Wander mode default
        self._state: str = self._base_emotion
        
        # Physiological state
        self._is_blinking: bool = False
        self._blink_end: float = 0.0
        self._next_blink: float = time.monotonic() + self._rand(BLINK_MIN, BLINK_MAX)
        
        # Conversation state
        self._is_conversing: bool = False
        self._idle_return_time: float = float('inf')

        # Real-time streaming buffer
        self._stream_buffer: str = ""
        self._current_stream_emotion: str = "neutral"
        self._speak_mode_active: bool = False

        # Event wiring
        self._bus.subscribe("tick", self._on_tick, priority=80)
        self._bus.subscribe("user_spoke", self._on_user_spoke, priority=80)
        self._bus.subscribe("b_thinking", self._on_b_thinking, priority=80)
        self._bus.subscribe("b_finished_speaking", self._on_b_finished_speaking, priority=80)
        self._bus.subscribe("emotion_changed", self._on_external_emotion_change, priority=90)
        
        # Stream listeners
        self._bus.subscribe("llm_stream_chunk", self._on_llm_stream_chunk, priority=80)
        self._bus.subscribe("llm_response", self._on_llm_response, priority=80)
        self._bus.subscribe("b_set_behavior", self._on_set_behavior, priority=80)
        self._bus.subscribe("b_speak_mode_toggled", self._on_speak_mode_toggled, priority=80)

        logger.info("StateMachine initialized (Live-Stream Buffer Restored)")

    def _on_tick(self, payload: dict) -> None:
        now = time.monotonic()
        
        # Blinking Priority
        if self._is_blinking:
            if now >= self._blink_end:
                self._is_blinking = False
                self._next_blink = now + self._rand(BLINK_MIN, BLINK_MAX)
                self._bus.publish("emotion_changed", {"emotion": self._state, "intensity": 1.0})
            return

        if now >= self._next_blink:
            self._is_blinking = True
            self._blink_end = now + BLINK_DURATION
            self._bus.publish("emotion_changed", {"emotion": "blink", "intensity": 1.0})
            return

        # Idle Rest Return
        if not self._is_conversing and not self._speak_mode_active and self._state != self._base_emotion and now >= self._idle_return_time:
            self._set_emotion(self._base_emotion)
            self._idle_return_time = float('inf')

    def _on_user_spoke(self, payload: dict) -> None:
        self._is_conversing = True
        self._idle_return_time = float('inf')
        self._set_emotion("focused")

    def _on_b_thinking(self, payload: dict) -> None:
        self._is_conversing = True
        self._idle_return_time = float('inf')
        self._set_emotion("thinking")

    def _on_b_finished_speaking(self, payload: dict) -> None:
        self._is_conversing = False 
        self._idle_return_time = time.monotonic() + LINGER_DURATION

    def _on_external_emotion_change(self, payload: dict) -> None:
        new_emotion = payload.get("emotion", "neutral")
        if new_emotion != "blink" and self._state != new_emotion:
            self._state = new_emotion

    def _on_set_behavior(self, payload: dict) -> None:
        old_mode = self._behavior_mode
        new_mode = payload.get("mode", "wander")
        self._behavior_mode = new_mode
        
        if new_mode == "wander":
            self._base_emotion = "sad"
        elif new_mode == "corner":
            self._base_emotion = "sleeping"
        elif new_mode == "follow":
            self._base_emotion = "neutral"
        
        # Handle specific transition reactions
        reaction = None
        duration = 0.0
        
        if new_mode == "follow":
            if old_mode == "wander":
                reaction = "love_struck"
                duration = 2.0
                logger.info("Reaction: Love Struck (Wander -> Follow)")
            elif old_mode == "corner":
                reaction = "happy"
                duration = 1.0
                logger.info("Reaction: Happy (Corner -> Follow)")
        
        if reaction:
            self._set_emotion(reaction)
            self._idle_return_time = time.monotonic() + duration
        elif not self._is_conversing and not self._speak_mode_active:
            self._set_emotion(self._base_emotion)

    def _on_speak_mode_toggled(self, payload: dict) -> None:
        self._speak_mode_active = payload.get("active", False)
        if self._speak_mode_active:
            self._is_conversing = True
            self._idle_return_time = float('inf')
            self._set_emotion("focused")
        else:
            self._is_conversing = False
            self._idle_return_time = time.monotonic() + 0.5
            # State will return to base in next tick

    def _on_llm_stream_chunk(self, payload: dict) -> None:
        chunk = payload.get("text", "")
        self._stream_buffer += chunk
        
        # 1. Destroy AI conversational filler on the fly
        self._stream_buffer = self._stream_buffer.replace("<|assistant|>", "").replace("<|end|>", "")

        # 2. Parse sentences dynamically as tokens arrive
        while True:
            # Look for [TAG] text ending in punctuation
            match = re.search(r'\[([A-Z_]+)\]\s*([^\[]+?[.!?])(?=\s+|$)', self._stream_buffer, re.IGNORECASE)
            
            if not match:
                # Fallback if LLM forgets a tag mid-stream
                match = re.search(r'^([^\[]+?[.!?])(?=\s+|$)', self._stream_buffer)
            
            if match:
                if len(match.groups()) == 2:
                    tag, text = match.groups()
                    self._current_stream_emotion = map_emotion(tag)
                else:
                    text = match.group(1)
                
                full_match_text = match.group(0)
                clean_text = self._clean_text(text)
                
                if clean_text:
                    # Update the UI Chat Bubble instantly
                    self._bus.publish("b_spoke", {"text": clean_text, "emotion": self._current_stream_emotion})
                    # Push the sentence to the audio conductor instantly
                    self._bus.publish("b_sentence_ready", {"sentences": [{"emotion": self._current_stream_emotion, "text": clean_text}]})
                
                idx = self._stream_buffer.find(full_match_text)
                self._stream_buffer = self._stream_buffer[idx + len(full_match_text):].lstrip()
            
            else:
                # Look for [TAG] text... [NEXT_TAG]
                tag_break_match = re.search(r'\[([A-Z_]+)\]\s*([^\[]+?)\s*(?=\[)', self._stream_buffer, re.IGNORECASE)
                if tag_break_match:
                    tag, text = tag_break_match.groups()
                    self._current_stream_emotion = map_emotion(tag)
                        
                    clean_text = self._clean_text(text)
                    if clean_text:
                        self._bus.publish("b_spoke", {"text": clean_text, "emotion": self._current_stream_emotion})
                        self._bus.publish("b_sentence_ready", {"sentences": [{"emotion": self._current_stream_emotion, "text": clean_text}]})
                    
                    full_match_text = tag_break_match.group(0)
                    idx = self._stream_buffer.find(full_match_text)
                    self._stream_buffer = self._stream_buffer[idx + len(full_match_text):].lstrip()
                else:
                    break # Buffer needs more tokens

    def _on_llm_response(self, payload: dict) -> None:
        # Flush any remaining text at the very end of generation
        if self._stream_buffer.strip():
            # Check if there's a final tag we haven't processed
            tag_match = re.match(r'\[([A-Z_]+)\]', self._stream_buffer, re.IGNORECASE)
            if tag_match:
                self._current_stream_emotion = map_emotion(tag_match.group(1))
                # Remove the tag from the text we're about to speak
                self._stream_buffer = self._stream_buffer[tag_match.end():].lstrip()

            clean_text = self._clean_text(self._stream_buffer)
            if clean_text:
                self._bus.publish("b_spoke", {"text": clean_text, "emotion": self._current_stream_emotion})
                self._bus.publish("b_sentence_ready", {"sentences": [{"emotion": self._current_stream_emotion, "text": clean_text}]})
        
        self._stream_buffer = ""

    def _clean_text(self, text: str) -> str:
        clean = re.sub(r'\*.*?\*', '', text)
        clean = re.sub(r'\[.*?\]', '', clean)
        clean = re.sub(r'\(.*?\)', '', clean)
        return clean.strip()

    def _set_emotion(self, emotion: str) -> None:
        if self._state != emotion:
            self._state = emotion
            self._bus.publish("emotion_changed", {"emotion": self._state, "intensity": 1.0})

    @staticmethod
    def _rand(lo: float, hi: float) -> float:
        return random.uniform(lo, hi)