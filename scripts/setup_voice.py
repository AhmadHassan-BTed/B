import os
import sys
import urllib.request

VOICES_DIR = "voices"
os.makedirs(VOICES_DIR, exist_ok=True)

# Using Ryan High (Clear, Human-like with a slight robotic cadence)
URL_ONNX = "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/high/en_US-ryan-high.onnx?download=true"
URL_JSON = "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/high/en_US-ryan-high.onnx.json?download=true"

dest_onnx = os.path.join(VOICES_DIR, "b_voice.onnx")
dest_json = os.path.join(VOICES_DIR, "b_voice.onnx.json")

def download_file(url, dest):
    print(f"Downloading {dest}...")
    try:
        urllib.request.urlretrieve(url, dest)
        print("Done.")
    except Exception as e:
        print(f"Failed to download: {e}")
        sys.exit(1)

if not os.path.exists(dest_onnx) or os.path.getsize(dest_onnx) < 1000000:
    download_file(URL_ONNX, dest_onnx)
    
if not os.path.exists(dest_json):
    download_file(URL_JSON, dest_json)

print("Testing audio output...")
try:
    from piper.voice import PiperVoice
    import sounddevice as sd
    import numpy as np

    voice = PiperVoice.load(dest_onnx)
    text = "Audio diagnostic complete. I am fully online."
    
    audio_stream = voice.synthesize_stream_raw(text)
    raw_audio = b''.join(audio_stream)
    audio_array = np.frombuffer(raw_audio, dtype=np.int16)
    
    sd.play(audio_array, samplerate=voice.config.sample_rate)
    sd.wait()
    print("SUCCESS: Audio played successfully.")
except Exception as e:
    print(f"ERROR testing audio: {e}")
