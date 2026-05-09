# Privacy Policy

**B** is designed with a "Local-First" and "Privacy-First" philosophy. We believe your desktop data is yours and should stay on your machine.

## 🔒 Data Handling

### 👁️ Vision and Perception
* **B** observes your screen to provide contextual assistance.
* All OCR and UI tracking are performed **locally** on your CPU/GPU.
* Screenshots taken for visual analysis are processed in-memory and are **not saved** to disk permanently, nor are they ever uploaded to our servers.

### 🧠 Cognitive Engine (LLM)
* **Local Inference (Default)**: If you use the `llama-cpp` backend, all "thinking" happens entirely on your machine. No text is sent over the internet.
* **Cloud Inference (Optional)**: If you choose to use Groq or Gemini APIs, your context (screen text and history) is sent to those respective providers. Please refer to their privacy policies.

### 🎙️ Audio and Voice
* **Neural Hearing**: Speech-to-Text (Faster-Whisper) is performed locally.
* **Vocal Synthesis**: Text-to-Speech (Piper) is performed locally.
* Your voice data never leaves your machine.

### 📝 Logs
* B generates logs (`b_session.log`, `b_analytics.log`) for debugging and memory.
* These logs stay on your local machine in the project directory.
* We recommend checking these logs before sharing them in issue reports to ensure no sensitive information is included.

## 📡 Connectivity
B does not have a "phone home" feature. It does not track usage statistics, crash reports (unless you manually submit them), or user behavior.

## 🤝 Your Responsibility
As an open-source project, you have full visibility into the code. We encourage you to audit the source code to verify these claims.
