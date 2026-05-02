"""
sensors/ears.py — B's Hearing (Faster-Whisper)
═══════════════════════════════════════════════

Uses Faster-Whisper for high-quality local STT.
Uses sounddevice for cross-platform audio capture.
Fires 'user_hearing' (partial) and 'user_spoke' (final) events.
"""

from __future__ import annotations

import logging
import threading
import time
import queue
import random
from typing import TYPE_CHECKING, Optional

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel

if TYPE_CHECKING:
    from core.bus import EventBus

logger = logging.getLogger("B.sensors.ears")

# ──────────────────────────────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────────────────────────────
MODEL_SIZE = "tiny.en"  # tiny.en is fast and accurate enough for English
SAMPLE_RATE = 16000     # Whisper expects 16kHz
CHANNELS = 1            # Mono
CHUNK_SIZE = 1024       # Buffer size for sounddevice
SILENCE_THRESHOLD = 0.01 # RMS threshold for "speech" vs "silence"
MIN_SPEECH_DURATION = 0.8 # Seconds of speech before we start transcribing
MAX_BUFFER_DURATION = 10.0 # Maximum seconds to buffer

class EarsSensor:
    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._is_active = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        
        # Audio processing state
        self._audio_queue = queue.Queue()
        self._model: Optional[WhisperModel] = None
        self._buffer = np.array([], dtype=np.float32)
        
        # Performance/Behavior tuning
        self._last_transcription_time = 0
        self._transcription_interval = 0.3 # Seconds between partial transcriptions
        self._silence_start = None
        self._speech_detected = False

    def _lazy_load_model(self):
        if self._model is None:
            # Increase threads and workers for better performance on 12-core system
            self._model = WhisperModel(
                MODEL_SIZE, 
                device="cpu", 
                compute_type="int8",
                cpu_threads=4,
                num_workers=2
            )
            logger.info("Faster-Whisper model loaded.")

    def start_listening(self) -> None:
        if self._is_active:
            return
            
        self._is_active = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._listening_loop, daemon=True)
        self._thread.start()
        logger.info("Ears (Whisper) starting listening loop.")

    def stop_listening(self) -> None:
        if not self._is_active:
            return
            
        self._is_active = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=1.0)
        logger.info("Ears (Whisper) stopped.")

    def _audio_callback(self, indata, frames, time, status):
        """This is called from the sounddevice input thread."""
        if status:
            logger.warning("Audio status: %s", status)
        
        # Periodic energy check (every ~1s)
        if random.random() < 0.05:
            rms = np.sqrt(np.mean(indata**2))
            logger.debug("Incoming audio RMS: %.4f", rms)
            
        self._audio_queue.put(indata.copy())

    def _listening_loop(self) -> None:
        """Main loop for audio capture and transcription."""
        self._lazy_load_model()
        
        try:
            with sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                callback=self._audio_callback,
                blocksize=CHUNK_SIZE,
                dtype=np.float32
            ):
                logger.info("Audio stream open. Listening...")
                
                while not self._stop_event.is_set():
                    # 1. Collect all available audio chunks from the queue
                    while not self._audio_queue.empty():
                        chunk = self._audio_queue.get()
                        self._buffer = np.append(self._buffer, chunk.flatten())
                    
                    # 2. Limit buffer size
                    max_samples = int(SAMPLE_RATE * MAX_BUFFER_DURATION)
                    if len(self._buffer) > max_samples:
                        self._buffer = self._buffer[-max_samples:]
                    
                    # 3. Detect speech/silence (very simple VAD)
                    if len(self._buffer) > 0:
                        rms = np.sqrt(np.mean(self._buffer[-int(SAMPLE_RATE*0.3):]**2))
                        is_currently_speaking = rms > SILENCE_THRESHOLD
                        
                        if is_currently_speaking:
                            if not self._speech_detected:
                                logger.info("Speech detected (RMS: %.4f)", rms)
                            self._speech_detected = True
                            self._silence_start = None
                        elif self._speech_detected:
                            if self._silence_start is None:
                                self._silence_start = time.time()
                            
                            # If silent for > 1.2 seconds, consider the sentence finished
                            if time.time() - self._silence_start > 1.2:
                                self._finalize_transcription()
                                continue

                    # 4. Perform partial transcription for "real-time typing" (in background)
                    now = time.time()
                    if self._speech_detected and (now - self._last_transcription_time > self._transcription_interval):
                        self._last_transcription_time = now
                        threading.Thread(target=self._partial_transcription, daemon=True).start()
                    
                    time.sleep(0.05)
                    
        except Exception as e:
            logger.exception("Ears (Whisper) loop crashed: %s", e)
        finally:
            self._is_active = False

    def _partial_transcription(self):
        """Transcribe current buffer and fire 'user_hearing'."""
        # Partial feedback should be ultra-fast, so we start after only 0.3s of audio
        if len(self._buffer) < int(SAMPLE_RATE * 0.3):
            return
            
        try:
            # transcribe() is quite fast on tiny.en
            segments, _ = self._model.transcribe(
                self._buffer, 
                beam_size=1, 
                language="en",
                vad_filter=False # More lenient for partials
            )
            
            text = " ".join([s.text for s in segments]).strip()
            if text:
                logger.info("Ears published user_hearing: '%s'", text)
                self._bus.publish("user_hearing", {"text": text})
            else:
                logger.debug("Ears partial transcription was empty")
        except Exception:
            pass # Ignore errors in partial transcription

    def _finalize_transcription(self):
        """Transcribe final buffer, fire 'user_spoke', and clear buffer."""
        if len(self._buffer) < int(SAMPLE_RATE * MIN_SPEECH_DURATION):
            self._reset_state()
            return
            
        try:
            segments, _ = self._model.transcribe(
                self._buffer, 
                beam_size=5, # Higher beam size for final result
                language="en",
                vad_filter=True
            )
            
            text = " ".join([s.text for s in segments]).strip()
            if text:
                logger.info("Whisper heard: %s", text)
                self._bus.publish("user_spoke", {"text": text, "source": "voice"})
            
        except Exception as e:
            logger.error("Final transcription error: %s", e)
        finally:
            self._reset_state()

    def _reset_state(self):
        self._buffer = np.array([], dtype=np.float32)
        self._speech_detected = False
        self._silence_start = None
