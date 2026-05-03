import os
import sys
import subprocess
import shutil
import time

try:
    from piper.voice import PiperVoice
    import sounddevice as sd
    import numpy as np
    from pedalboard import Pedalboard, PitchShift, Bitcrush, Chorus, Gain
except ImportError:
    print("Error: Required packages not found.")
    print("Please run: pip install piper-tts sounddevice numpy scipy pedalboard")
    sys.exit(1)

# Ensure absolute paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VOICES_DIR = os.path.join(BASE_DIR, "voices")
os.makedirs(VOICES_DIR, exist_ok=True)

# The single perfect voice for the Robo-DSP pipeline (High pitched, bright, energetic)
URL_ONNX = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_GB/jenny_dioco/medium/en_GB-jenny_dioco-medium.onnx"
URL_JSON = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_GB/jenny_dioco/medium/en_GB-jenny_dioco-medium.onnx.json"

dest_onnx = os.path.join(VOICES_DIR, "b_voice.onnx")
dest_json = os.path.join(VOICES_DIR, "b_voice.onnx.json")

def download_with_curl(url: str, dest: str):
    print(f"Downloading {os.path.basename(dest)} using curl...")
    # curl.exe is native in Windows 10/11 and much more reliable than urllib
    result = subprocess.run(["curl.exe", "-L", url, "-o", dest, "--progress-bar"])
    if result.returncode != 0:
        print(f"Failed to download {url}")
        sys.exit(1)

def main():
    print("=== B's Robot Voice Setup ===")
    
    # Download if it doesn't exist or is corrupted (less than 1MB)
    if not os.path.exists(dest_onnx) or os.path.getsize(dest_onnx) < 1000000:
        download_with_curl(URL_ONNX, dest_onnx)
    
    if not os.path.exists(dest_json):
        download_with_curl(URL_JSON, dest_json)
        
    print("\nModel ready. Testing the Robo-DSP audio pipeline...\n")
    
    try:
        voice = PiperVoice.load(dest_onnx)
        text = "YAAAY! We did it!! I am fully online and ready to help!"
        print(f"Speaking: '{text}'")
        
        import wave
        import io
        
        sample_rate = voice.config.sample_rate
        
        wav_io = io.BytesIO()
        with wave.open(wav_io, 'wb') as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            voice.synthesize_wav(text, wav_file)
            
        wav_io.seek(0)
        with wave.open(wav_io, 'rb') as wav_file:
            raw_audio = wav_file.readframes(wav_file.getnframes())
        
        raw_array = np.frombuffer(raw_audio, dtype=np.int16)
        
        # Apply the exact DSP filter used in audio/speaker.py
        board = Pedalboard([
            PitchShift(semitones=5.0),
            Bitcrush(bit_depth=12),
            Chorus(rate_hz=2.0, depth=0.08, centre_delay_ms=7.0, feedback=0.0, mix=0.25),
            Gain(gain_db=1.0)
        ])
        
        float_audio = raw_array.astype(np.float32) / 32768.0
        processed_float = board(float_audio, sample_rate, reset=False)
        processed_int16 = np.clip(processed_float * 32768.0, -32768, 32767).astype(np.int16)
        
        sd.play(processed_int16, samplerate=sample_rate)
        sd.wait()
        
        print("\nSuccess! The custom Robot Voice is perfectly installed and verified.")
        print("You can now safely close this script and run main.py.")
    except Exception as e:
        print(f"Verification Failed: {e}")

if __name__ == "__main__":
    main()
