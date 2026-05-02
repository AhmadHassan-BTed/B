import winocr
from PIL import Image

img = Image.new('RGB', (200, 100), color=(255, 255, 255))
result = winocr.recognize_pil(img)
print(f"Type: {type(result)}")
print(f"Dir: {dir(result)}")
