import os
import sys
import urllib.request
import shutil

try:
    from piper.voice import PiperVoice
    import sounddevice as sd
    import numpy as np
    from pedalboard import Pedalboard, PitchShift, Bitcrush, Chorus, Gain
except ImportError:
    print("Error: Required packages not found.")
    print("Please run: pip install piper-tts sounddevice numpy scipy pedalboard")
    sys.exit(1)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODELS_DIR = os.path.join(BASE_DIR, "models")
VOICES_DIR = os.path.join(BASE_DIR, "voices")
os.makedirs(MODELS_DIR, exist_ok=True)
os.makedirs(VOICES_DIR, exist_ok=True)

# The 3 Curated "Cute/Expressive" Voices for B
VOICES = {
    "1": {
        "name": "The Animated Sprite (High pitched, energetic)",
        "file": "en_GB-jenny_dioco-medium.onnx",
        "url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_GB/jenny_dioco/medium/en_GB-jenny_dioco-medium.onnx",
        "json_url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_GB/jenny_dioco/medium/en_GB-jenny_dioco-medium.onnx.json"
    },
    "2": {
        "name": "The Friendly Helper (Soft, approachable)",
        "file": "en_US-amy-medium.onnx",
        "url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/amy/medium/en_US-amy-medium.onnx",
        "json_url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/amy/medium/en_US-amy-medium.onnx.json"
    },
    "3": {
        "name": "The Little Buddy (Expressive, bouncy cadence)",
        "file": "en_GB-alba-medium.onnx",
        "url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_GB/alba/medium/en_GB-alba-medium.onnx",
        "json_url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_GB/alba/medium/en_GB-alba-medium.onnx.json"
    }
}

def download_file(url: str, dest: str):
    if os.path.exists(dest):
        return
    print(f"  Downloading {os.path.basename(dest)}...")
    
    def reporthook(blocknum, blocksize, totalsize):
        readsofar = blocknum * blocksize
        if totalsize > 0:
            percent = readsofar * 100.0 / totalsize
            sys.stdout.write(f"\r  Progress: {percent:.1f}%")
            sys.stdout.flush()
    
    try:
        urllib.request.urlretrieve(url, dest, reporthook)
        print("\n  Download complete.")
    except Exception as e:
        print(f"\n  Error downloading: {e}")
        sys.exit(1)

def ensure_models_exist():
    print("Checking and downloading voice models...")
    for key, data in VOICES.items():
        base_path = os.path.join(MODELS_DIR, data["file"])
        json_path = base_path + ".json"
        
        if not os.path.exists(base_path) or not os.path.exists(json_path):
            print(f"\nNeed to download: {data['name']}")
            download_file(data["url"], base_path)
            download_file(data["json_url"], json_path)
    print("\nAll models are ready!\n")

def test_voice(voice_id: str):
    data = VOICES[voice_id]
    model_path = os.path.join(MODELS_DIR, data["file"])
    
    print(f"\nLoading {data['name']}...")
    voice = PiperVoice.load(model_path)
    
    text = "Hii! I am B! I'm ready to get to work. How does my voice sound?"
    print(f"Speaking: '{text}'")
    
    import wave
    import io
    
    sample_rate = voice.config.sample_rate
    
    # Synthesize to raw audio stream
    wav_io = io.BytesIO()
    with wave.open(wav_io, 'wb') as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        voice.synthesize_wav(text, wav_file)
        
    wav_io.seek(0)
    with wave.open(wav_io, 'rb') as wav_file:
        raw_audio = wav_file.readframes(wav_file.getnframes())
    
    # Convert bytes to numpy int16 array
    raw_array = np.frombuffer(raw_audio, dtype=np.int16)
    
    # Apply B's Robot DSP to let the user hear the ACTUAL final sound
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
    sd.wait() # Wait until audio finishes playing

def main():
    ensure_models_exist()
    
    while True:
        print("=== B Voice Selector ===")
        for key, data in VOICES.items():
            print(f"[{key}] {data['name']}")
        print("[Q] Quit")
        print("========================")
        
        choice = input("Select a voice to test (1-3) or Q to quit: ").strip().lower()
        
        if choice == 'q':
            print("Exiting without selection.")
            break
            
        if choice in VOICES:
            test_voice(choice)
            
            confirm = input(f"\nDo you want to finalize and use {VOICES[choice]['name']} for B? (y/n): ").strip().lower()
            if confirm == 'y':
                source_onnx = os.path.join(MODELS_DIR, VOICES[choice]["file"])
                source_json = source_onnx + ".json"
                
                dest_onnx = os.path.join(VOICES_DIR, "b_voice.onnx")
                dest_json = os.path.join(VOICES_DIR, "b_voice.onnx.json")
                
                shutil.copy2(source_onnx, dest_onnx)
                shutil.copy2(source_json, dest_json)
                
                print(f"\nSuccess! B will now use {VOICES[choice]['name']}.")
                print("The voice has been saved as models/b_voice.onnx")
                break
            else:
                print("\nLet's try another one.\n")
        else:
            print("Invalid choice. Please try again.\n")

if __name__ == "__main__":
    main()