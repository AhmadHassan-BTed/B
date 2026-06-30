"""
sensors/ears.py — B's Hearing (Production Grade)
═══════════════════════════════════════════════════════════════════════════════

A production-level STT pipeline that mirrors how real voice assistants work.

Architecture
────────────
  ┌─────────────┐    VAD chunks     ┌─────────────────┐   audio buffer
  │ sounddevice │ ───────────────▶  │  Silero VAD      │ ──────────────▶ Whisper
  │  InputStream│                   │  (per-32ms prob) │
  └─────────────┘                   └─────────────────┘
                                           │
                            ┌──────────────┴──────────────┐
                       is_speech?                    barge-in monitor
                            │                         (while TTS plays)
                    ┌───────┴────────┐                      │
                 LISTENING     SPEECH_ACTIVE          user_interrupted ──▶ bus
                    │               │
              pre-buffer        accumulate
              (ring 300ms)    + partial Whisper
                                    │
                             silence > 1.2s
                                    │
                             PROCESSING ──▶ final Whisper ──▶ user_spoke ──▶ bus

Key Features
────────────
  • Silero VAD           — neural VAD, robust to background noise, fans, keyboards
  • Pre-speech ring buf  — captures 300 ms before VAD fires; no clipped first syllables
  • Full state machine   — IDLE → LISTENING → SPEECH_ACTIVE → PROCESSING
  • Barge-in detection   — listens for user speech while TTS plays; fires user_interrupted
  • Echo suppression     — higher VAD threshold + ignores buffer while assistant speaks
  • High-pass filter     — strips HVAC/fan rumble below 80 Hz before VAD
  • Adaptive carry buf   — VAD chunk alignment without dropping samples
  • Thread-safe          — all shared state behind locks; no data races
  • Graceful shutdown    — clean join on stop_listening()

Event Contract
──────────────
  Publishes:
      "user_hearing"      {"text": str}                   rolling partial transcript
      "user_spoke"        {"text": str, "source": "voice"} final committed utterance
      "user_interrupted"  {}                              barge-in while TTS played

  Subscribes:
      "assistant_speaking_start"  {}   TTS has begun  → engage barge-in mode
      "assistant_speaking_end"    {}   TTS is done    → return to normal listening

Dependencies
────────────
  pip install faster-whisper sounddevice torch silero-vad scipy numpy
  # Silero VAD is downloaded from torch hub on first run (~2 MB, cached afterward)
"""

from __future__ import annotations

import collections
import enum
import logging
import threading
import time
import queue
import math
from typing import TYPE_CHECKING, Optional

import numpy as np
import sounddevice as sd
import torch
from scipy.signal import butter, sosfilt, resample_poly
from faster_whisper import WhisperModel

if TYPE_CHECKING:
    from core.bus import EventBus

logger = logging.getLogger("B.audio.ears")


# ══════════════════════════════════════════════════════════════════════
# Tuneable constants
# ══════════════════════════════════════════════════════════════════════

# ── Audio hardware ────────────────────────────────────────────────────
SAMPLE_RATE       = 16_000        # Hz — Whisper and Silero both expect 16 kHz
VAD_CHUNK_SAMPLES = 512           # Silero requires exactly 512 samples @ 16 kHz ≈ 32 ms
BLOCKSIZE         = 1_024         # sounddevice block size (must be ≥ VAD_CHUNK_SAMPLES)

# ── Timing ────────────────────────────────────────────────────────────
PRE_SPEECH_PAD_S   = 0.30   # Ring buffer depth — captures audio BEFORE VAD fires
SILENCE_HANGOVER_S = 1.20   # Silence duration before we finalize the utterance
MIN_SPEECH_S       = 0.40   # Utterances shorter than this are discarded (spurious clicks)
MAX_SPEECH_S       = 15.0   # Hard ceiling — finalize even if the user never pauses
PARTIAL_INTERVAL_S = 0.45   # Minimum seconds between partial transcription emissions

# ── VAD thresholds (Silero probability 0.0 → 1.0) ────────────────────
VAD_SPEECH_PROB       = 0.50   # Normal listening — balanced sensitivity
VAD_BARGE_IN_PROB     = 0.80   # While TTS plays — high bar rejects TTS bleed-through
BARGE_IN_MIN_SPEECH_S = 0.25   # Sustained user speech required before firing barge-in

# ── Whisper ───────────────────────────────────────────────────────────
WHISPER_MODEL   = "small.en"   # tiny.en=fastest, small.en=best quality/speed trade-off
WHISPER_THREADS = 6
WHISPER_WORKERS = 2

# ── Audio preprocessing ───────────────────────────────────────────────
HIGHPASS_CUTOFF_HZ = 80    # Cut frequencies below this (HVAC, electrical hum)
HIGHPASS_ORDER     = 4     # Butterworth filter order


# ══════════════════════════════════════════════════════════════════════
# Internal helpers
# ══════════════════════════════════════════════════════════════════════

class _State(enum.Enum):
    IDLE          = "idle"
    LISTENING     = "listening"
    SPEECH_ACTIVE = "speech_active"
    PROCESSING    = "processing"


class _RingBuffer:
    """
    Fixed-capacity audio ring buffer backed by a deque of chunks.

    Pushes new chunks, automatically evicting the oldest samples when
    the total exceeds `max_samples`. drain() returns and clears all
    held audio as a single contiguous array.
    """

    def __init__(self, max_samples: int) -> None:
        self._chunks: collections.deque[np.ndarray] = collections.deque()
        self._max    = max_samples
        self._held   = 0

    def push(self, chunk: np.ndarray) -> None:
        self._chunks.append(chunk)
        self._held += len(chunk)
        # Evict oldest chunks until we are within budget
        while self._held > self._max and self._chunks:
            evicted   = self._chunks.popleft()
            self._held -= len(evicted)

    def drain(self) -> np.ndarray:
        """Return all buffered audio and clear the ring."""
        if not self._chunks:
            return np.array([], dtype=np.float32)
        out = np.concatenate(list(self._chunks))
        self._chunks.clear()
        self._held = 0
        return out

    def clear(self) -> None:
        self._chunks.clear()
        self._held = 0


def _build_highpass(cutoff_hz: float = HIGHPASS_CUTOFF_HZ,
                    order: int = HIGHPASS_ORDER,
                    sr: int = SAMPLE_RATE) -> np.ndarray:
    """Return scipy SOS coefficients for a high-pass Butterworth filter."""
    nyq = sr / 2.0
    return butter(order, cutoff_hz / nyq, btype="high", output="sos")


# ══════════════════════════════════════════════════════════════════════
# EarsSensor
# ══════════════════════════════════════════════════════════════════════

class EarsSensor:
    """
    Production-grade hearing sensor for a voice assistant.

    See module docstring for the full architecture and event contract.
    """

    def __init__(self, bus: "EventBus") -> None:
        self._bus = bus

        # ── State machine ────────────────────────────────────────────
        self._state      = _State.IDLE
        self._state_lock = threading.Lock()

        # ── Models (lazy — loaded on first start_listening call) ─────
        self._whisper: Optional[WhisperModel] = None
        self._vad_model = None          # Silero VAD torch module

        # ── Audio pipeline ──────────────────────────────────────────
        self._audio_q: "queue.Queue[np.ndarray]" = queue.Queue()

        # Ring buffer holds PRE_SPEECH_PAD_S of audio before VAD fires
        self._pre_buf = _RingBuffer(int(SAMPLE_RATE * PRE_SPEECH_PAD_S))

        # Accumulates audio during an active utterance
        self._speech_chunks: list[np.ndarray] = []

        # Leftover samples that didn't fill a full VAD chunk
        self._vad_carry = np.array([], dtype=np.float32)

        # High-pass filter SOS (built once, applied per chunk)
        self._hp_sos: np.ndarray = _build_highpass()

        # ── Timing bookkeeping ───────────────────────────────────────
        self._speech_start_t:  float          = 0.0
        self._silence_start_t: Optional[float] = None
        self._last_partial_t:  float          = 0.0

        # ── Barge-in / echo suppression ─────────────────────────────
        self._assistant_speaking = False
        self._actual_sr          = SAMPLE_RATE
        self._barge_speech_s     = 0.0         # Accumulated speech while TTS active
        self._barge_lock         = threading.Lock()

        # ── Control ─────────────────────────────────────────────────
        self._stop_evt = threading.Event()
        self._main_thread: Optional[threading.Thread] = None

        # ── Subscribe to TTS lifecycle events ────────────────────────
        bus.subscribe("assistant_speaking_start", self._on_tts_start)
        bus.subscribe("assistant_speaking_end",   self._on_tts_end)

    # ──────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────

    def start_listening(self) -> None:
        """Open the microphone stream and begin the VAD + transcription pipeline."""
        if self._main_thread and self._main_thread.is_alive():
            logger.warning("EarsSensor.start_listening() called while already active")
            return
        self._stop_evt.clear()
        self._main_thread = threading.Thread(
            target=self._main_loop,
            name="ears-main",
            daemon=True,
        )
        self._main_thread.start()
        logger.info("EarsSensor: starting …")

    def stop_listening(self) -> None:
        """Signal the pipeline to shut down and block until it exits."""
        self._stop_evt.set()
        if self._main_thread:
            self._main_thread.join(timeout=4.0)
            if self._main_thread.is_alive():
                logger.warning("EarsSensor main thread did not exit cleanly")
        logger.info("EarsSensor: stopped.")

    # ──────────────────────────────────────────────────────────────────
    # Bus callbacks
    # ──────────────────────────────────────────────────────────────────

    def _on_tts_start(self, _payload: dict) -> None:
        """Called when the assistant begins speaking — activates barge-in monitor."""
        with self._barge_lock:
            self._assistant_speaking = True
            self._barge_speech_s     = 0.0
        logger.debug("EarsSensor: TTS started → barge-in mode ACTIVE")

    def _on_tts_end(self, _payload: dict) -> None:
        """Called when TTS finishes — returns to normal listening mode."""
        with self._barge_lock:
            self._assistant_speaking = False
            self._barge_speech_s     = 0.0
        logger.debug("EarsSensor: TTS ended → normal listening resumed")

    # ──────────────────────────────────────────────────────────────────
    # Model loading (lazy, first call only)
    # ──────────────────────────────────────────────────────────────────

    def _load_models(self) -> None:
        if self._vad_model is None:
            logger.info("Loading Silero VAD …")
            # Cached in ~/.cache/torch/hub after the first download
            self._vad_model, _ = torch.hub.load(
                "snakers4/silero-vad",
                "silero_vad",
                force_reload=False,
                onnx=False,
            )
            self._vad_model.eval()
            self._vad_model.reset_states()
            logger.info("Silero VAD: loaded ")

        if self._whisper is None:
            logger.info("Loading Faster-Whisper [%s] …", WHISPER_MODEL)
            self._whisper = WhisperModel(
                WHISPER_MODEL,
                device="cpu",
                compute_type="int8",
                cpu_threads=WHISPER_THREADS,
                num_workers=WHISPER_WORKERS,
            )
            logger.info("Faster-Whisper: loaded ")

    # ──────────────────────────────────────────────────────────────────
    # sounddevice input callback
    # ──────────────────────────────────────────────────────────────────

    def _audio_callback(self,
                        indata:  np.ndarray,
                        frames:  int,
                        t,
                        status) -> None:
        """
        Called from the sounddevice input thread for every audio block.
        Must return quickly — heavy work happens in _main_loop.
        """
        if status:
            logger.warning("Audio callback status: %s", status)

        # Collapse stereo to mono (mean channel average)
        mono = indata[:, 0] if indata.ndim > 1 else indata.flatten()
        self._audio_q.put(mono.astype(np.float32, copy=False))

    # ──────────────────────────────────────────────────────────────────
    # Main pipeline loop
    # ──────────────────────────────────────────────────────────────────

    def _main_loop(self) -> None:
        """
        Opens the microphone stream and drives the VAD + state machine.
        Runs in its own daemon thread.
        """
        self._load_models()

        device_idx, n_channels = self._select_input_device()
        info = sd.query_devices(device_idx, "input")
        device_default_sr = int(info["default_samplerate"])
        
        logger.info("EarsSensor: using device #%d (%d ch, default %d Hz)", 
                    device_idx, n_channels, device_default_sr)

        # Try to open at 16kHz first. If that fails, fall back to the device's default.
        try:
            stream = sd.InputStream(
                device=device_idx,
                samplerate=SAMPLE_RATE,
                channels=n_channels,
                callback=self._audio_callback,
                blocksize=BLOCKSIZE,
                dtype=np.float32,
            )
            self._actual_sr = SAMPLE_RATE
        except Exception as e:
            logger.info("EarsSensor: 16kHz not supported by device (%s). Falling back to %d Hz.", e, device_default_sr)
            stream = sd.InputStream(
                device=device_idx,
                samplerate=device_default_sr,
                channels=n_channels,
                callback=self._audio_callback,
                blocksize=int(BLOCKSIZE * (device_default_sr / SAMPLE_RATE)),
                dtype=np.float32,
            )
            self._actual_sr = device_default_sr

        try:
            with stream:
                logger.info("EarsSensor: audio stream LIVE (%d Hz) — listening …", self._actual_sr)
                self._set_state(_State.LISTENING)

                while not self._stop_evt.is_set():
                    self._process_queued_audio()
                    self._check_silence_hangover()
                    time.sleep(0.005)   # 5 ms spin — keeps CPU near 0%

        except Exception:
            logger.exception("EarsSensor main loop crashed")
        finally:
            self._set_state(_State.IDLE)

    # ──────────────────────────────────────────────────────────────────
    # Core processing — called every ~5 ms from main loop
    # ──────────────────────────────────────────────────────────────────

    def _process_queued_audio(self) -> None:
        """
        Drain the audio queue, align to VAD_CHUNK_SAMPLES boundaries,
        apply the high-pass filter, and drive the state machine for each chunk.
        """
        raw_chunks: list[np.ndarray] = []
        try:
            while True:
                raw_chunks.append(self._audio_q.get_nowait())
        except queue.Empty:
            pass

        if not raw_chunks:
            return

        # Prepend any carry-over from the previous call
        raw_audio = np.concatenate(raw_chunks)
        
        # ── Resample to 16kHz if necessary ───────────────────────────
        if self._actual_sr != SAMPLE_RATE:
            # Simplify the fraction for resample_poly
            common = math.gcd(SAMPLE_RATE, self._actual_sr)
            up = SAMPLE_RATE // common
            down = self._actual_sr // common
            audio_16k = resample_poly(raw_audio, up, down).astype(np.float32)
        else:
            audio_16k = raw_audio

        audio = np.concatenate([self._vad_carry, audio_16k])

        n_full = len(audio) // VAD_CHUNK_SAMPLES
        self._vad_carry = audio[n_full * VAD_CHUNK_SAMPLES:]  # save remainder

        for i in range(n_full):
            raw_chunk = audio[i * VAD_CHUNK_SAMPLES: (i + 1) * VAD_CHUNK_SAMPLES]

            # ── High-pass filter: remove HVAC / fan rumble ───────────
            chunk = sosfilt(self._hp_sos, raw_chunk).astype(np.float32)

            # ── VAD probability ──────────────────────────────────────
            prob = self._get_vad_prob(chunk)

            # ── Feed state machine ───────────────────────────────────
            self._advance_state_machine(chunk, prob)

    def _get_vad_prob(self, chunk: np.ndarray) -> float:
        """
        Query Silero VAD for the probability that `chunk` contains speech.
        Returns a float in [0.0, 1.0].
        """
        tensor = torch.from_numpy(chunk).unsqueeze(0)   # shape: [1, 512]
        with torch.no_grad():
            return float(self._vad_model(tensor, SAMPLE_RATE).item())

    # ──────────────────────────────────────────────────────────────────
    # State machine
    # ──────────────────────────────────────────────────────────────────

    def _advance_state_machine(self, chunk: np.ndarray, prob: float) -> None:
        """
        Core FSM.  One call per 32 ms VAD chunk.

        While TTS is playing:
            → run barge-in monitor only; do NOT accumulate in speech_buf
        While idle / listening:
            → maintain pre-speech ring buffer
            → on speech: transition to SPEECH_ACTIVE
        While SPEECH_ACTIVE:
            → accumulate audio
            → emit partials
            → on silence hangover or max duration: finalize
        """
        # ── Read shared barge-in state ───────────────────────────────
        with self._barge_lock:
            tts_active = self._assistant_speaking

        chunk_duration_s = VAD_CHUNK_SAMPLES / SAMPLE_RATE  # ≈ 0.032 s

        # ╔══════════════════════════════════════════════════════════╗
        # ║  BARGE-IN MONITOR (while assistant is speaking)          ║
        # ╚══════════════════════════════════════════════════════════╝
        if tts_active:
            is_user_speech = prob >= VAD_BARGE_IN_PROB

            with self._barge_lock:
                if is_user_speech:
                    self._barge_speech_s += chunk_duration_s
                    if self._barge_speech_s >= BARGE_IN_MIN_SPEECH_S:
                        logger.info(
                            "EarsSensor: barge-in detected (%.2f s of speech, prob=%.2f)",
                            self._barge_speech_s,
                            prob,
                        )
                        self._bus.publish("user_interrupted", {})
                        # Reset so we don't fire again until next TTS round
                        self._barge_speech_s = -BARGE_IN_MIN_SPEECH_S
                else:
                    # Decay: brief noise spikes shouldn't accumulate
                    self._barge_speech_s = max(
                        0.0,
                        self._barge_speech_s - chunk_duration_s * 1.5,
                    )
            return  # ← Do NOT build a speech buffer while TTS is playing

        # ╔══════════════════════════════════════════════════════════╗
        # ║  NORMAL LISTENING                                         ║
        # ╚══════════════════════════════════════════════════════════╝
        state      = self._get_state()
        is_speech  = prob >= VAD_SPEECH_PROB

        # ── LISTENING: waiting for the user to start speaking ────────
        if state == _State.LISTENING:
            self._pre_buf.push(chunk)

            if is_speech:
                # Pull the pre-speech ring buffer so we don't clip the first word
                preamble = self._pre_buf.drain()
                self._speech_chunks = [preamble, chunk] if len(preamble) else [chunk]

                self._speech_start_t  = time.time()
                self._silence_start_t = None
                self._last_partial_t  = time.time()

                self._set_state(_State.SPEECH_ACTIVE)
                logger.debug("VAD: speech start (prob=%.2f)", prob)

        # ── SPEECH_ACTIVE: user is speaking ──────────────────────────
        elif state == _State.SPEECH_ACTIVE:
            self._speech_chunks.append(chunk)

            if is_speech:
                self._silence_start_t = None   # Reset silence clock
            else:
                if self._silence_start_t is None:
                    self._silence_start_t = time.time()

            # Emit a rolling partial transcript
            now = time.time()
            if now - self._last_partial_t >= PARTIAL_INTERVAL_S:
                self._last_partial_t = now
                snapshot = np.concatenate(self._speech_chunks)
                threading.Thread(
                    target=self._emit_partial,
                    args=(snapshot,),
                    daemon=True,
                    name="ears-partial",
                ).start()

            # Hard ceiling — finalize even if the user never pauses
            if now - self._speech_start_t >= MAX_SPEECH_S:
                logger.info("EarsSensor: MAX_SPEECH_S hit — force finalizing")
                self._finalize_utterance()

        # ── PROCESSING: previous utterance is being transcribed ──────
        elif state == _State.PROCESSING:
            # Buffer quietly into the pre-speech ring so the next utterance
            # doesn't start with a clipped beginning.
            self._pre_buf.push(chunk)

    def _check_silence_hangover(self) -> None:
        """
        Called every loop iteration.  Finalizes the utterance once the user
        has been silent for SILENCE_HANGOVER_S seconds.
        """
        if self._get_state() != _State.SPEECH_ACTIVE:
            return
        if self._silence_start_t is None:
            return
        if time.time() - self._silence_start_t >= SILENCE_HANGOVER_S:
            logger.debug("EarsSensor: silence hangover expired — finalizing")
            self._finalize_utterance()

    # ──────────────────────────────────────────────────────────────────
    # Finalization
    # ──────────────────────────────────────────────────────────────────

    def _finalize_utterance(self) -> None:
        """
        Commit the accumulated speech buffer to a high-quality transcription.
        Transitions state to PROCESSING so new audio goes to the pre-buffer
        instead of being dropped.
        """
        if self._get_state() != _State.SPEECH_ACTIVE:
            return  # Guard against double-finalize

        self._set_state(_State.PROCESSING)

        # Grab and clear the speech buffer under minimal lock exposure
        audio = (
            np.concatenate(self._speech_chunks)
            if self._speech_chunks
            else np.array([], dtype=np.float32)
        )
        self._speech_chunks = []
        self._silence_start_t = None

        # Reset Silero's GRU state so the next utterance starts fresh
        if self._vad_model is not None:
            self._vad_model.reset_states()

        threading.Thread(
            target=self._transcribe_final,
            args=(audio,),
            daemon=True,
            name="ears-final",
        ).start()

    # ──────────────────────────────────────────────────────────────────
    # Transcription
    # ──────────────────────────────────────────────────────────────────

    def _emit_partial(self, audio: np.ndarray) -> None:
        """
        Fast partial transcription while the user is still speaking.
        Uses beam_size=1 and no VAD filter for minimum latency.
        Fires 'user_hearing'.
        """
        try:
            segs, _ = self._whisper.transcribe(
                audio,
                beam_size=1,
                language="en",
                vad_filter=False,
                condition_on_previous_text=False,
                temperature=0.0,
            )
            text = " ".join(s.text for s in segs).strip()
            if text:
                logger.debug("Partial → %r", text)
                self._bus.publish("user_hearing", {"text": text})

        except Exception:
            logger.debug("Partial transcription error (non-fatal)", exc_info=True)

    def _transcribe_final(self, audio: np.ndarray) -> None:
        """
        High-quality final transcription.  Runs in its own thread so the
        audio pipeline is never blocked.  Fires 'user_spoke' on success.
        Transitions back to LISTENING when done.
        """
        try:
            duration_s = len(audio) / SAMPLE_RATE

            # Discard utterances that are too short to be real speech
            if duration_s < MIN_SPEECH_S:
                logger.debug(
                    "EarsSensor: utterance too short (%.2f s < %.2f s) — discarded",
                    duration_s, MIN_SPEECH_S,
                )
                return

            segs, info = self._whisper.transcribe(
                audio,
                beam_size=5,                    # Higher quality than partials
                language="en",
                vad_filter=True,                # Let Whisper's own VAD clean up edges
                condition_on_previous_text=False,
                temperature=0.0,               # Greedy decode — more deterministic
                word_timestamps=False,
            )
            text = " ".join(s.text for s in segs).strip()

            logger.info(
                "Whisper final: %r  (dur=%.1fs, lang_prob=%.2f)",
                text, duration_s, info.language_probability,
            )

            if text:
                self._bus.publish("user_spoke", {"text": text, "source": "voice"})

        except Exception:
            logger.exception("EarsSensor: final transcription error")

        finally:
            # Always return to listening — even if transcription threw
            self._set_state(_State.LISTENING)

    # ──────────────────────────────────────────────────────────────────
    # Device selection
    # ──────────────────────────────────────────────────────────────────

    @staticmethod
    def _select_input_device() -> tuple[int, int]:
        """
        Choose the best available input device.

        Priority on Windows: WASAPI "Microphone Array" > any WASAPI input >
        default.  On other platforms falls through to the system default.
        Returns (device_index, channel_count).
        """
        try:
            apis = sd.query_hostapis()
            devs = sd.query_devices()

            # Collect candidates with a priority score
            candidates: list[tuple[int, int, str]] = []   # (score, idx, name)

            for idx, dev in enumerate(devs):
                if dev["max_input_channels"] < 1:
                    continue
                api_name = apis[dev["hostapi"]]["name"]
                name_lc  = dev["name"].lower()
                score    = 0

                if "WASAPI" in api_name:
                    score += 10
                if "array" in name_lc or "microphone" in name_lc:
                    score += 5
                if "virtual" in name_lc or "stereo mix" in name_lc:
                    score -= 20   # Avoid loopback devices

                candidates.append((score, idx, dev["name"]))

            if candidates:
                candidates.sort(reverse=True)
                best_score, best_idx, best_name = candidates[0]
                logger.info(
                    "EarsSensor: selected input '%s' (score=%d)", best_name, best_score
                )
                info = sd.query_devices(best_idx, "input")
                ch   = min(info["max_input_channels"], 2)
                return best_idx, ch

        except Exception:
            logger.warning("EarsSensor: device selection failed — using system default")

        # Fallback
        default_idx = sd.default.device[0]
        info        = sd.query_devices(default_idx, "input")
        ch          = min(info["max_input_channels"], 2)
        return default_idx, ch

    # ──────────────────────────────────────────────────────────────────
    # State helpers (thread-safe)
    # ──────────────────────────────────────────────────────────────────

    def _get_state(self) -> _State:
        with self._state_lock:
            return self._state

    def _set_state(self, new_state: _State) -> None:
        with self._state_lock:
            old = self._state
            self._state = new_state
        if old != new_state:
            logger.debug("EarsSensor: %s → %s", old.value, new_state.value)