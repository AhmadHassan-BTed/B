import winocr
from PIL import Image, ImageDraw

# Create a dummy image with text
img = Image.new('RGB', (200, 100), color=(255, 255, 255))
d = ImageDraw.Draw(img)
d.text((10, 10), "Hello B!", fill=(0, 0, 0))

try:
    result = winocr.recognize_pil(img)
    print(f"OCR Result: {result.text}")
except Exception as e:
    print(f"Error: {e}")
