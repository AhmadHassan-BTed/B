import os
import urllib.request
import sys

# Moondream2 is a tiny, high-performance vision model (only ~500MB)
URL = "https://huggingface.co/vikhyatk/moondream2/resolve/main/moondream2-q8_0.gguf"
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEST_DIR = os.path.join(BASE_DIR, "models")
DEST_PATH = os.path.join(DEST_DIR, "moondream2-q8_0.gguf")

os.makedirs(DEST_DIR, exist_ok=True)

def reporthook(blocknum, blocksize, totalsize):
    readsofar = blocknum * blocksize
    if totalsize > 0:
        percent = readsofar * 1e2 / totalsize
        s = "\r%5.1f%% %*d / %d bytes" % (
            percent, len(str(totalsize)), readsofar, totalsize)
        sys.stderr.write(s)
        if readsofar >= totalsize:
            sys.stderr.write("\n")
    else:
        sys.stderr.write("read %d\n" % (readsofar,))

if os.path.exists(DEST_PATH):
    print(f"Vision model already exists at {DEST_PATH}")
    sys.exit(0)

print(f"Downloading Vision Model ({URL})...")
print(f"To: {DEST_PATH}")
print("This is ~500MB and is optimized for low-resource systems.")

try:
    urllib.request.urlretrieve(URL, DEST_PATH, reporthook)
    print("\nDownload complete!")
except Exception as e:
    print(f"\nError downloading: {e}")
    sys.exit(1)
