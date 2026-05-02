
import time
import os
from llama_cpp import Llama

MODEL_PATH = "t:/B/models/phi-3-mini-4k-instruct-q4.gguf"

if not os.path.exists(MODEL_PATH):
    print(f"Model not found at {MODEL_PATH}")
    exit(1)

print("Loading model...")
llm = Llama(
    model_path=MODEL_PATH,
    n_ctx=2048,
    n_threads=max(1, os.cpu_count() - 1),
    verbose=True
)

prompt = "User: Hello B, how are you today?\nB:"
print(f"Starting inference with prompt: {prompt}")

start_time = time.time()
stream = llm(prompt, max_tokens=50, stream=True)

first_token_time = None
tokens = 0
for chunk in stream:
    if first_token_time is None:
        first_token_time = time.time()
        print(f"Time to first token: {first_token_time - start_time:.2f}s")
    
    text = chunk['choices'][0]['text']
    print(text, end="", flush=True)
    tokens += 1

end_time = time.time()
print(f"\n\nTotal time: {end_time - start_time:.2f}s")
print(f"Tokens: {tokens}")
print(f"Tokens per second: {tokens / (end_time - start_time):.2f}")
