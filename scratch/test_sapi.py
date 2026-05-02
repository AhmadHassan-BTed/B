import win32com.client
import time

try:
    # Try to create a Shared Recognizer (the one Windows uses)
    recognizer = win32com.client.Dispatch("SAPI.SpSharedRecognizer")
    print("SAPI Shared Recognizer created successfully!")
    
    # Create a context
    context = recognizer.CreateRecoContext()
    
    # Create a grammar
    grammar = context.CreateGrammar()
    grammar.DictationSetState(1) # 1 = SGDSActive (Listen to everything)
    
    print("SAPI STT is ready to listen!")
except Exception as e:
    print(f"SAPI STT failed: {e}")
