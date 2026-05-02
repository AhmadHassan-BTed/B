from faster_whisper import download_model
import os

model_name = "small.en"
print(f"Starting explicit download for Faster-Whisper model: {model_name}")
try:
    path = download_model(model_name)
    print(f"\nDownload complete! Model located at: {path}")
except Exception as e:
    print(f"\nError: {e}")
