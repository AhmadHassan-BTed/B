from huggingface_hub import snapshot_download
import os

repo_id = "Systran/faster-whisper-small.en"
print(f"Downloading {repo_id} via huggingface_hub...")
try:
    path = snapshot_download(repo_id=repo_id)
    print(f"\nDownload complete! Model located at: {path}")
except Exception as e:
    print(f"\nError: {e}")
