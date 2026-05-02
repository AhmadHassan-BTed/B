import winocr
from PIL import Image, ImageDraw

img = Image.new('RGB', (200, 100), color=(255, 255, 255))
d = ImageDraw.Draw(img)
d.text((10, 10), "Hello B!", fill=(0, 0, 0))

try:
    # winocr.recognize_pil returns an IAsyncOperation in the recent winrt versions
    # We need to call .get() to block and get the OcrResult
    res_obj = winocr.recognize_pil(img)
    result = res_obj.get()
    print(f"OCR Result: {result.text}")
except Exception as e:
    print(f"Error: {e}")
    # Try another way
    import asyncio
    async def run_ocr():
        result = await winocr.recognize_pil(img)
        print(f"Async OCR Result: {result.text}")
    try:
        asyncio.run(run_ocr())
    except Exception as e2:
        print(f"Async Error: {e2}")
