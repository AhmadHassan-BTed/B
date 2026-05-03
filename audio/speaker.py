"""
audio/speaker.py — The Robo-DSP Pipeline
════════════════════════════════════════

Subscribes to 'b_spoke' and synthesizes a cute, emotionally expressive
robot voice locally. It uses Piper TTS for the base voice, and processes
it through a real-time DSP filter (Pedalboard) for a mechanical texture.
It also procedurally generates pre-speech non-verbal sounds based on emotion!
"""

from __future__ import annotations

import logging
import os
import queue
import threading
import io
import wave
from typing import TYPE_CHECKING, Tuple, Optional

from PyQt6.QtCore import QObject, pyqtSignal
import numpy as np

try:
    import sounddevice as sd
    from scipy.signal import square
    from pedalboard import Pedalboard, PitchShift, Bitcrush, Chorus, Gain
except ImportError:
    pass # Handled in VoiceEngine._audio_worker

if TYPE_CHECKING:
    from core.bus import EventBus

logger = logging.getLogger("B.audio.speaker")

# Ensure MODEL_PATH is absolute relative to this file's location
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_PATH = os.path.join(BASE_DIR, "voices", "b_voice.onnx")

# =====================================================================
# DSP CONFIGURATION - TUNABLE PARAMETERS FOR B's ROBOT VOICE
# =====================================================================

# Pitch shift (semitones): Upward shift makes the voice sound smaller/cuter.
ROBOT_PITCH_SHIFT = 5.0  

# Bitcrush depth (bits): Lower means more "crunchy/retro". 
# 12 is a very soft, tiny digital modulation (not harsh).
ROBOT_BIT_DEPTH = 12     

# Chorus settings: Gives a "soft vocoder touch", not heavy sci-fi.
# Very fast rate, but extremely low depth and mix so it's just a texture.
ROBOT_CHORUS_RATE = 2.0   
ROBOT_CHORUS_DEPTH = 0.08 
ROBOT_CHORUS_MIX = 0.25   

# Base output volume gain (dB)
ROBOT_OUTPUT_GAIN = 1.0   

# =====================================================================

class RobotSFX:
    """Procedurally generates short non-verbal robot sound effects Husing numpy."""
    
    def __init__(self, sample_rate: int = 22050):
        self.sr = sample_rate

    def _generate_sine_sweep(self, start_freq: float, end_freq: float, duration: float) -> np.ndarray:
        """Generates a sine wave that sweeps from start_freq to end_freq."""
        t = np.linspace(0, duration, int(self.sr * duration), False)
        # Logarithmic sweep
        k = (end_freq / start_freq) ** (1 / duration)
        phase = 2 * np.pi * start_freq * ((k ** t - 1) / np.log(k))
        # Fade in/out to prevent clicking
        audio = np.sin(phase)
        fade_len = int(self.sr * 0.02) # 20ms fade
        if len(audio) > fade_len * 2:
            audio[:fade_len] *= np.linspace(0, 1, fade_len)
            audio[-fade_len:] *= np.linspace(1, 0, fade_len)
        return (audio * 32767 * 0.5).astype(np.int16)

    def _generate_beeps(self, freq: float, duration: float, count: int, gap: float) -> np.ndarray:
        """Generates a series of square wave blips."""
        t = np.linspace(0, duration, int(self.sr * duration), False)
        blip = square(2 * np.pi * freq * t)
        
        # Soften the square wave a bit
        fade_len = int(len(blip) * 0.1)
        blip[:fade_len] *= np.linspace(0, 1, fade_len)
        blip[-fade_len:] *= np.linspace(1, 0, fade_len)
        
        silence = np.zeros(int(self.sr * gap))
        
        sequence = []
        for _ in range(count):
            sequence.append(blip)
            sequence.append(silence)
            
        audio = np.concatenate(sequence)
        return (audio * 32767 * 0.2).astype(np.int16)

    def get_sfx_for_emotion(self, emotion: str) -> Optional[np.ndarray]:
        """Returns a pre-speech sound effect based on the emotion string."""
        emotion = emotion.lower()
        
        if emotion in ["happy", "excited", "joy"]:
            # Fast, bouncy, bright "bwee!"
            return self._generate_sine_sweep(900, 1800, 0.1)
            
        elif emotion in ["sad", "tired", "sorry"]:
            # Soft, sad "boooop"
            return self._generate_sine_sweep(900, 500, 0.3)
            
        elif emotion in ["thinking", "confused", "curious"]:
            # Processing "tik tik tik"
            return self._generate_beeps(1500, 0.03, 3, 0.04)
            
        elif emotion in ["angry", "frustrated"]:
            # Tiny little angry buzz
            return self._generate_beeps(300, 0.15, 2, 0.05)
            
        elif emotion in ["idle", "neutral", "normal"]:
            # Tiny chirp "Bip! Hi!"
            return self._generate_sine_sweep(1400, 1600, 0.04)
            
        return None

class RobotVocoder:
    """Applies real-time DSP to make human TTS sound like a cute robot."""
    
    def __init__(self, sample_rate: int):
        self.sample_rate = sample_rate
        self.board = Pedalboard([
            PitchShift(semitones=ROBOT_PITCH_SHIFT),
            Bitcrush(bit_depth=ROBOT_BIT_DEPTH),
            Chorus(
                rate_hz=ROBOT_CHORUS_RATE, 
                depth=ROBOT_CHORUS_DEPTH, 
                centre_delay_ms=7.0, 
                feedback=0.0, 
                mix=ROBOT_CHORUS_MIX
            ),
            Gain(gain_db=ROBOT_OUTPUT_GAIN)
        ])

    def process_audio(self, raw_audio: np.ndarray, emotion: str = "neutral") -> np.ndarray:
        """Passes the raw numpy audio through the pedalboard effects with emotional pitch adjustment."""
        # Map emotion to pitch shift (semitones)
        # Higher = cuter/excited, Lower = serious/angry
        pitch_map = {
            "love_struck": 9.5,
            "in_love": 9.0,
            "happy": 7.5,
            "excited": 8.0,
            "neutral": 5.0,
            "sad": 3.0,
            "disgusted": 2.5,
            "confused": 6.0,
            "angry": -2.0,   # Much deeper!
            "serious": 1.5
        }
        
        # Default to neutral if emotion not found
        emotion_key = emotion.lower()
        semitones = pitch_map.get(emotion_key, 5.0)
        
        # Update the PitchShift (0) and Bitcrush (1) plugins
        try:
            self.board[0].semitones = semitones
            # Make "angry" voice heavy and gritty with lower bit depth
            self.board[1].bit_depth = 8.0 if emotion_key == "angry" else 12.0
        except Exception:
            pass # Safety for plugin index
            
        # Pedalboard expects float32 in range [-1, 1]
        float_audio = raw_audio.astype(np.float32) / 32768.0
        
        # Process audio. Pedalboard is heavily optimized in C++.
        processed_float = self.board(float_audio, self.sample_rate, reset=False)
        
        # Convert back to int16
        processed_int16 = np.clip(processed_float * 32768.0, -32768, 32767).astype(np.int16)
        return processed_int16

class VoiceEngine(QObject):
    _publish_signal = pyqtSignal(str, dict)

    def __init__(self, bus: EventBus) -> None:
        super().__init__()
        self._bus = bus
        self._queue = queue.Queue()
        self._voice = None
        self._sample_rate = 22050
        
        # Connect signal to the synchronous bus publish
        self._publish_signal.connect(self._on_publish_requested)
        
        self._sfx_engine: Optional[RobotSFX] = None
        self._vocoder: Optional[RobotVocoder] = None
        
        # Background thread unconditionally started to prevent blocking
        self._thread = threading.Thread(target=self._audio_worker, daemon=True, name="AudioWorker")
        self._thread.start()

        self._bus.subscribe("b_spoke", self._on_b_spoke, priority=90)
        self._bus.subscribe("user_spoke", self._on_user_spoke, priority=90)
        self._bus.subscribe("user_interrupted", self._on_user_spoke, priority=90) # Interruption stops playback too
        self._bus.subscribe("b_thinking", self._on_b_thinking, priority=90)
        self._bus.subscribe("llm_response", self._on_llm_response, priority=90)
        
        self._llm_thinking = False
        logger.info("Robo-VoiceEngine initialized")

    def _on_publish_requested(self, event_name: str, payload: dict) -> None:
        """Publishes an event to the bus. This method is called on the main thread via the signal."""
        self._bus.publish(event_name, payload)

    def _init_tts(self) -> bool:
        """Loads the TTS model on the background thread."""
        if self._voice is not None:
            return True
            
        if not os.path.exists(MODEL_PATH):
            return False
            
        try:
            from piper.voice import PiperVoice
            self._voice = PiperVoice.load(MODEL_PATH)
            self._sample_rate = self._voice.config.sample_rate
            
            # Initialize DSP components now that we know the sample rate
            self._sfx_engine = RobotSFX(self._sample_rate)
            self._vocoder = RobotVocoder(self._sample_rate)
            
            logger.info("Piper TTS & DSP Vocoder loaded successfully.")
            return True
        except ImportError:
            logger.error("piper-tts is not installed.")
            return False
        except Exception as e:
            logger.error("Failed to load Voice Engine: %s", e)
            return False

    def _on_b_spoke(self, payload: dict) -> None:
        """
        Event callback for when B generates text.
        Payload expects 'text' and optional 'emotion'.
        """
        text = payload.get("text", "").strip()
        emotion = payload.get("emotion", "idle")
        if text:
            # Push a tuple of (text, emotion) to the queue
            self._queue.put((text, emotion))

    def _on_user_spoke(self, payload: dict) -> None:
        """Instantly interrupts B if he is currently speaking."""
        self._llm_thinking = True # User speaking means LLM will start thinking soon
        try:
            import sounddevice as sd
            # Check if we are actually playing or have something queued
            is_playing = False
            try:
                is_playing = sd.get_stream().active
            except:
                pass
                
            if is_playing or not self._queue.empty():
                sd.stop()
                with self._queue.mutex:
                    self._queue.queue.clear()
                logger.info("Voice playback interrupted by user.")
        except Exception:
            pass

    def _on_b_thinking(self, payload: dict) -> None:
        self._llm_thinking = True

    def _on_llm_response(self, payload: dict) -> None:
        self._llm_thinking = False

    def _audio_worker(self) -> None:
        """
        Background worker thread. Blocks on queue, synthesizes, filters, and plays audio.
        """
        try:
            import sounddevice as sd
            import numpy as np
        except ImportError:
            logger.error("Required audio libraries (sounddevice, numpy, scipy, pedalboard) missing. Voice disabled.")
            return

        self._init_tts()

        while True:
            payload = self._queue.get()
            text, emotion = payload
            
            if not self._init_tts():
                logger.warning("Voice model not loaded, dropping speech.")
                self._queue.task_done()
                continue
                
            try:
                # 1. Play Pre-Speech Emotion SFX
                if self._sfx_engine:
                    sfx_audio = self._sfx_engine.get_sfx_for_emotion(emotion)
                    if sfx_audio is not None and len(sfx_audio) > 0:
                        sd.play(sfx_audio, samplerate=self._sample_rate)
                        sd.wait()
                
                # 2. Synthesize TTS
                import wave
                import io
                wav_io = io.BytesIO()
                
                # Add a trailing space to ensure Piper doesn't cut off the last word
                safe_text = text + " "
                
                with wave.open(wav_io, 'wb') as wav_file:
                    wav_file.setnchannels(1)
                    wav_file.setsampwidth(2)
                    wav_file.setframerate(self._sample_rate)
                    self._voice.synthesize_wav(safe_text, wav_file)
                
                wav_io.seek(0)
                with wave.open(wav_io, 'rb') as wav_file:
                    raw_audio = wav_file.readframes(wav_file.getnframes())
                
                if raw_audio:
                    raw_array = np.frombuffer(raw_audio, dtype=np.int16)
                    
                    # Pad the array with 1.2 seconds of silence to ensure NO audio is lost in Pedalboard's delay/pitch buffers
                    padding = np.zeros(int(self._sample_rate * 1.2), dtype=np.int16)
                    padded_array = np.concatenate((raw_array, padding))
                    
                    # 3. Apply DSP Filter (Vocoder) with Emotional Pitch
                    if self._vocoder:
                        processed_array = self._vocoder.process_audio(padded_array, emotion)
                    else:
                        processed_array = padded_array
                        
                    # Trigger the face and UI change exactly when the audio starts
                    # We MUST emit via signal to ensure this happens on the main GUI thread!
                    self._publish_signal.emit("assistant_speaking_start", {})
                    self._publish_signal.emit("emotion_changed", {"emotion": emotion, "intensity": 1.0})
                    self._publish_signal.emit("b_playing_sentence", {"text": text, "emotion": emotion})

                    # 4. Play spoken audio synchronously
                    sd.play(processed_array, samplerate=self._sample_rate, blocking=True)
                    
                    self._publish_signal.emit("assistant_speaking_end", {})
                    
                    import time
                    time.sleep(0.1) # Brief safety sleep for sound card flush
                    
                    # Only signal "finished" if there's nothing else immediately waiting in the queue
                    # AND the LLM is not currently generating more sentences.
                    if self._queue.empty() and not self._llm_thinking:
                        logger.info("Audio playback finished and LLM idle, publishing b_finished_speaking")
                        self._publish_signal.emit("b_finished_speaking", {})
                    
            except Exception as e:
                logger.exception("Error during robo-audio synthesis: %s", e)
                # Ensure end signal is sent even on error
                self._publish_signal.emit("assistant_speaking_end", {})
            finally:
                self._queue.task_done()
