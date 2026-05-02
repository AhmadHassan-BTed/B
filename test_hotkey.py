import ctypes
import time
from ctypes import wintypes

# Virtual keys
VK_CONTROL = 0x11
VK_SHIFT = 0x10
VK_MENU = 0x12 # Alt
VK_B = 0x42
VK_RETURN = 0x0D

KEYEVENTF_KEYUP = 0x0002

user32 = ctypes.windll.user32

def press_key(vk):
    user32.keybd_event(vk, 0, 0, 0)

def release_key(vk):
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)

def type_string(s):
    for c in s:
        vk = ctypes.windll.user32.VkKeyScanW(ord(c)) & 0xFF
        press_key(vk)
        release_key(vk)
        time.sleep(0.05)

print("Sending Ctrl+Shift+Alt+B...")
press_key(VK_CONTROL)
press_key(VK_SHIFT)
press_key(VK_MENU)
press_key(VK_B)

time.sleep(0.1)

release_key(VK_B)
release_key(VK_MENU)
release_key(VK_SHIFT)
release_key(VK_CONTROL)

print("Waiting for input box to appear...")
time.sleep(1.0)

print("Typing 'Hello B!'")
type_string("Hello B!")

time.sleep(0.5)
press_key(VK_RETURN)
release_key(VK_RETURN)

print("Done. Check B's logs!")
