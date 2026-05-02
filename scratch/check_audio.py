import sounddevice as sd
print(sd.query_devices())
print("\nDefault Input Device:", sd.default.device[0])
