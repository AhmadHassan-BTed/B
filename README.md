<div align="center">

![B Hero Banner](docs/assets/hero_banner.png)

# ─── B ───
### *The Soul-Wired Desktop Companion*

[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![Qt](https://img.shields.io/badge/PyQt6-6.6.0+-41CD52?style=for-the-badge&logo=qt&logoColor=white)](https://www.qt.io/)
[![License](https://img.shields.io/badge/License-MIT-F7DF1E?style=for-the-badge)](LICENSE)
[![Privacy](https://img.shields.io/badge/Privacy-First-green?style=for-the-badge)](PRIVACY.md)
[![Status](https://img.shields.io/badge/Status-Active_Development-blueviolet?style=for-the-badge)](https://github.com/Ahmad-Hassan-0/B---desktop-companion)

**B** is not just an AI; he is a digital lifeform designed to live on your desktop. Built with a "soul-first" architecture, B observes your workflow, listens with neural precision, and interacts through a high-performance, glassmorphic overlay.

[Explore the Vision](#-the-vision) • [Architecture](docs/ARCHITECTURE.md) • [Contributing](CONTRIBUTING.md) • [Privacy](PRIVACY.md) • [Getting Started](#-installation)

</div>

---

##  The Vision

Traditional assistants wait for a command. **B** waits for a moment. 

B is designed as a **proactive desktop companion**. Using a 60fps event-driven "Central Nervous System," B synchronizes his emotional state with your environment. He sees your screen semantically, tracks your active focus, and intervenes only when he has something truly valuable to contribute.

- **Silent by Default**: B respects your "Deep Work" state.
- **Emotionally Aware**: A complex internal state machine drives expressions and curiosity.
- **Agentic Autonomy**: B doesn't just respond; he thinks, wonders, and observes.

---

##  Core Systems


| System               | Technology              | Description                                                          |
| :------------------- | :---------------------- | :------------------------------------------------------------------- |
| **Cognitive Engine** | `Llama-CPP`             | Local LLM inference for private, high-speed reasoning.               |
| **Semantic Vision**  | `OCR + Window Tracking` | B understands the context of your active windows and screen content. |
| **Neural Hearing**   | `Faster-Whisper`        | Industry-grade transcription with neural VAD for reliable "ears."    |
| **Vocal Synthesis**  | `Piper TTS`             | Low-latency, natural-sounding voice for fluid interaction.           |
| **Kinematics**       | `PyQt6 Physics`         | Smooth, 60fps movement and fluid UI animations.                      |

---

##  Agentic "Work Mode"

Activated via `Ctrl+Shift+Alt+W`, Work Mode shifts B into a high-utility state:
- **Semantic Monitoring**: B monitors your progress on tasks in real-time.
- **Contextual Curiosity**: Proactively offers insights, documentation, or suggestions based on your current focus.
- **Minimalist Presence**: Dims facial expressions to minimize distraction while remaining vigilant.

---

##  [SETUP]  Tech Stack

<div align="center">

![Python](https://img.shields.io/badge/python-3670A0?style=for-the-badge&logo=python&logoColor=ffdd54)
![Qt](https://img.shields.io/badge/Qt-%23217346.svg?style=for-the-badge&logo=Qt&logoColor=white)
![PyWin32](https://img.shields.io/badge/Windows-0078D6?style=for-the-badge&logo=windows&logoColor=white)
![AI/ML](https://img.shields.io/badge/AI/ML-FF6F00?style=for-the-badge&logo=google-cloud&logoColor=white)

</div>

B is built for the Windows ecosystem, leveraging low-level Win32 APIs for transparent overlays and global input handling, combined with the power of modern neural models for perception.

---

##  [STOP]  Safety & Kill Switch

Human-centric safety is baked into the core. Because B lives on a transparent, click-through overlay without a standard "X" button, we've implemented a global emergency exit:

> [!CAUTION]
> **GLOBAL KILL SWITCH**: `Ctrl+Shift+Alt+Q`
> This hotkey immediately terminates B and releases all system hooks. Use this if B becomes over-eager or if you need an instant exit.

---

##  [START]  Installation

1. **Clone the Soul**:
   ```bash
   git clone https://github.com/Ahmad-Hassan-0/B---desktop-companion.git
   cd B---desktop-companion
   ```

2. **Initialize the Environment**:
   ```bash
   python -m venv venv
   source venv/bin/activate  # or venv\Scripts\activate
   pip install -r requirements.txt
   ```

3. **Awaken B**:
   ```bash
   python main.py
   ```

---

<div align="center">

Built with  by [Ahmad Hassan](https://github.com/Ahmad-Hassan-0)
*Wiring the soul, one tick at a time.*

</div>
