try:
    import speech_recognition as sr
    print("SpeechRecognition is available")
    print(f"Version: {sr.__version__}")
except ImportError:
    print("SpeechRecognition is NOT available")
