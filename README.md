<div align="center">

![B Hero Banner](docs/assets/hero_banner.png)

# ─── B ───
### *The Soul-Wired Desktop Companion*

[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![Qt](https://img.shields.io/badge/PyQt6-6.6.0+-41CD52?style=for-the-badge&logo=qt&logoColor=white)](https://www.qt.io/)
[![License](https://img.shields.io/badge/License-MIT-F7DF1E?style=for-the-badge)](LICENSE)
[![Privacy](https://img.shields.io/badge/Privacy-First-green?style=for-the-badge)](PRIVACY.md)
[![Status](https://img.shields.io/badge/Status-Active_Development-blueviolet?style=for-the-badge)](https://github.com/Ahmad-Hassan-0/B---desktop-companion)

**B** is not just an AI : he is a digital lifeform designed to live on your desktop. Built with a soul-first architecture, B observes your workflow, listens with neural precision, and interacts through a high-performance, glassmorphic overlay.

[Explore the Vision](#-the-vision) • [Architecture](#-architecture-overview) • [Getting Started](#-installation) • [Contributing](CONTRIBUTING.md) • [Privacy](PRIVACY.md)

</div>

<br>

<div align="center">

## Demo

<video src="docs/assets/demo-video.mp4" controls width="100%" style="max-width: 800px; border-radius: 12px;"></video>

*B in action - watch him observe, think, and respond.*

</div>

---

## The Vision

Traditional assistants wait for a command. **B** waits for a moment.

B is designed as a **proactive desktop companion**. Using a 60fps event-driven central nervous system, B synchronizes his emotional state with your environment. He sees your screen semantically, tracks your active focus, and intervenes only when he has something truly valuable to contribute.

- **Silent by Default** : B respects your deep work state.
- **Emotionally Aware** : A complex internal state machine drives expressions and curiosity.
- **Agentic Autonomy** : B doesn't just respond; he thinks, wonders, and observes.

---

## Architecture Overview

B is built on a **centralized asynchronous pub/sub event bus** : the `EventBus`. Every module communicates exclusively through this bus. No module knows about any other module. This strict decoupling makes the system testable, maintainable, and resilient.

```mermaid
flowchart TB
    subgraph P["Perception Layer"]
        WT["WindowTracker"]
        SS["SemanticSensor"]
        VS["VisionSensor OCR"]
    end

    subgraph C["Cognitive Layer"]
        CE["CognitiveEngine"]
        SM["StateMachine"]
        AE["AutonomyEngine"]
    end

    subgraph O["Output Layer"]
        VE["VoiceEngine"]
        FR["FaceRenderer"]
        CB["ChatBubble"]
        KE["KinematicsEngine"]
    end

    subgraph I["Core Infrastructure"]
        EB["EventBus"]
    end

    WT -- "active_window_changed" --> EB
    SS -- "context_updated" --> EB
    VS -- "context_updated" --> EB
    EB -- "context_updated" --> CE
    EB -- "tick" --> SM
    EB -- "tick" --> KE
    CE -- "b_spoke" --> EB
    EB -- "b_spoke" --> VE
    EB -- "b_spoke" --> FR
    EB -- "b_spoke" --> CB
    CE -- "b_move_request" --> EB
    EB -- "b_move_request" --> KE
    AE -- "trigger_proactive_thought" --> EB
    EB -- "trigger_proactive_thought" --> CE
```

---

## System Workflow

The data flows through B in a deterministic pipeline: **Perception → Cognition → Expression**.

```mermaid
sequenceDiagram
    participant WT as WindowTracker
    participant SS as SemanticSensor
    participant VS as VisionSensor
    participant CE as CognitiveEngine
    participant VE as VoiceEngine
    participant FR as FaceRenderer

    WT->>SS: active_window_changed
    activate SS
    SS->>SS: UIA Tree Walk
    alt Extraction Success
        SS->>CE: context_updated
    else Failure / Cooldown
        SS->>VS: semantic_extraction_failed
        VS->>VS: OCR Capture
        VS->>CE: context_updated
    end
    deactivate SS

    CE->>CE: LLM Inference
    CE->>VE: b_spoke (Text + Emotion)
    activate VE
    VE->>VE: TTS + DSP Vocoder
    VE->>FR: speaking_start
    VE->>VE: Audio Output
    VE->>FR: speaking_end
    deactivate VE
    CE->>FR: b_move_request (Spatial)
```

---

## Internal Module Structure

### Core Infrastructure

| Module       | File          | Responsibility                                                                                   |
| :----------- | :------------ | :----------------------------------------------------------------------------------------------- |
| **EventBus** | `core/bus.py` | Thread-safe pub/sub message broker. All inter-module communication flows through this.           |
| **main.py**  | `main.py`     | Boot sequence : instantiates all modules, starts the 60fps tick timer, registers global hotkeys. |

### Perception Layer (Sensors)

| Module             | File                        | Responsibility                                                                                             |
| :----------------- | :-------------------------- | :--------------------------------------------------------------------------------------------------------- |
| **WindowTracker**  | `sensors/window_tracker.py` | Hook-based active window change detection. Fires when the user switches focus.                             |
| **SemanticSensor** | `vision/semantic.py`        | UIA-based DOM/window tree walking. Extracts structured content with quality scoring and adaptive cooldown. |
| **VisionSensor**   | `vision/mss_capture.py`     | OCR fallback pipeline using MSS + Tesseract for frameworks incompatible with UIA.                          |

### Cognitive Layer (The Brain)

| Module              | File                     | Responsibility                                                                                                                       |
| :------------------ | :----------------------- | :----------------------------------------------------------------------------------------------------------------------------------- |
| **CognitiveEngine** | `brain/llm.py`           | LLM inference orchestration (Groq cloud or local llama-cpp). Manages context, history, spatial mapping, and streaming token parsing. |
| **StateMachine**    | `brain/soul.py`          | B's emotional state : blinking, resting, conversing. Real-time stream buffer that parses LLM output into sentences.                  |
| **AutonomyEngine**  | `brain/autonomy_loop.py` | Proactive thought scheduling : decides when B should speak unprompted based on context quality and timing.                           |

### Output Layer (Expression)

| Module               | File                    | Responsibility                                                                                |
| :------------------- | :---------------------- | :-------------------------------------------------------------------------------------------- |
| **VoiceEngine**      | `audio/speaker.py`      | Piper ONNX TTS with DSP vocoder chain (pitch shift, bitcrush, chorus) for robotic modulation. |
| **FaceRenderer**     | `ui/face.py`            | PyQt6 QPainter-based hardware-accelerated face rendering at 60fps.                            |
| **ChatBubble**       | `ui/chat.py`            | Glassmorphic chat overlay that displays B's spoken text.                                      |
| **KinematicsEngine** | `physics/kinematics.py` | Physics-based movement with Bezier path interpolation and easing curves.                      |
| **EarsSensor**       | `audio/ears.py`         | Speech-to-text via Faster-Whisper with neural VAD.                                            |

---

## Data Flow

```mermaid
flowchart LR
    subgraph Input["Input"]
        A["User Types"]
        B["User Speaks"]
        C["Screen Changes"]
    end

    subgraph Process["Processing"]
        D["EventBus"]
        E["CognitiveEngine"]
        F["StateMachine"]
    end

    subgraph Output["Output"]
        G["FaceRenderer"]
        H["VoiceEngine"]
        I["ChatBubble"]
        J["Kinematics"]
    end

    A -- types --> D
    B -- speaks --> D
    C -- changes --> D
    D -- routes --> E
    E -- drives --> F
    F -- animates --> G
    F -- speaks --> H
    F -- displays --> I
    E -- moves --> J
```

---

## Request Lifecycle

```mermaid
stateDiagram-v2
    [*] --> Idle
    Idle --> Listening : user_spoke / voice detected
    Listening --> Thinking : 1.2s delay
    Thinking --> Streaming : first token received
    Streaming --> Speaking : sentence_ready
    Speaking --> Streaming : next sentence
    Streaming --> Idle : [SILENCE] / end of response
    Speaking --> Idle : finished_speaking + linger
    Idle --> Proactive : autonomy trigger
    Proactive --> Thinking : context available
    Proactive --> Idle : no context / silence
```

---

## Project Structure

```
B/
├── main.py                  # Entry point : boot sequence
├── core/
│   └── bus.py               # EventBus : central nervous system
├── brain/
│   ├── llm.py               # CognitiveEngine : LLM inference
│   ├── soul.py              # StateMachine : emotions & stream buffer
│   ├── autonomy_loop.py     # AutonomyEngine : proactive thought
│   ├── context.py           # Context management
│   └── work_mode.py         # Work mode prompt templates
├── vision/
│   ├── semantic.py          # SemanticSensor : UIA extraction
│   └── mss_capture.py       # VisionSensor : OCR fallback
├── sensors/
│   └── window_tracker.py    # WindowTracker : focus detection
├── audio/
│   ├── speaker.py           # VoiceEngine : TTS + DSP
│   └── ears.py              # EarsSensor : STT
├── physics/
│   └── kinematics.py        # KinematicsEngine : movement
├── ui/
│   ├── overlay.py           # WindowManager : transparent overlay
│   ├── face.py              # FaceRenderer : 60fps face
│   ├── chat.py              # ChatBubble : text overlay
│   ├── input_box.py         # InputBox : text input
│   ├── expressions.py       # Expression definitions
│   └── theme.py             # Visual theming
├── models/                  # Local GGUF models (gitignored)
├── voices/                  # Piper ONNX voice models
├── scripts/                 # Setup & download utilities
├── docs/
│   ├── ARCHITECTURE.md      # Detailed architecture docs
│   └── assets/              # Images & diagrams
├── .env                     # API keys (gitignored)
└── requirements.txt         # Python dependencies
```

---

## Core Systems

| System               | Technology                       | Description                                                              |
| :------------------- | :------------------------------- | :----------------------------------------------------------------------- |
| **Cognitive Engine** | `Groq API` / `llama-cpp`         | Cloud or local LLM inference for private, high-speed reasoning.          |
| **Semantic Vision**  | `UIAutomation` + `Tesseract OCR` | B understands the context of your active windows and screen content.     |
| **Neural Hearing**   | `Faster-Whisper`                 | Industry-grade transcription with neural VAD for reliable ears.          |
| **Vocal Synthesis**  | `Piper TTS` + `Pedalboard DSP`   | Low-latency, natural-sounding voice with robotic modulation effects.     |
| **Kinematics**       | `PyQt6 QPropertyAnimation`       | Smooth, 60fps movement with Bezier path interpolation and easing curves. |
| **Event Bus**        | `PyQt6 pyqtSignal`               | Thread-safe pub/sub message broker : the central nervous system.         |

---

## Agentic Work Mode

Activated via `Ctrl+Shift+Alt+W`, Work Mode shifts B into a high-utility state:

- **Semantic Monitoring** : B monitors your progress on tasks in real-time.
- **Contextual Curiosity** : Proactively offers insights, documentation, or suggestions based on your current focus.
- **Minimalist Presence** : Dims facial expressions to minimize distraction while remaining vigilant.

```mermaid
flowchart TB
    A["User presses Ctrl+Shift+Alt+W"] --> B["B asks: what are you working on?"]
    B --> C["User defines goal"]
    C --> D["B monitors screen context"]
    D --> E{"Relevant content?"}
    E -- "Yes" --> F["B offers insight / help"]
    E -- "No" --> G["B stays silent"]
    F --> D
    G --> D
```

---

## Hotkeys

| Shortcut           | Action                                                                    |
| :----------------- | :------------------------------------------------------------------------ |
| `Ctrl+Shift+Alt+Q` | **Kill switch** : immediately terminates B and releases all system hooks. |
| `Ctrl+Shift+Alt+B` | Toggle input box : type messages to B.                                    |
| `Ctrl+Shift+Alt+V` | Toggle speak mode : talk to B via microphone.                             |
| `Ctrl+Shift+Alt+W` | Toggle work mode : B becomes a proactive assistant.                       |

---

## Installation

1. **Clone the repository**:
   ```bash
   git clone https://github.com/Ahmad-Hassan-0/B---desktop-companion.git
   cd B---desktop-companion
   ```

2. **Set up the environment**:
   ```bash
   python -m venv venv
   venv\Scripts\activate      # Windows
   source venv/bin/activate   # Linux/macOS
   pip install -r requirements.txt
   ```

3. **Configure API keys**:
   ```bash
   cp .env.example .env
   # Edit .env with your Groq API key (get one at https://console.groq.com/)
   ```

4. **Awaken B**:
   ```bash
   python main.py
   ```

---

## Safety

> [!CAUTION]
> **Global Kill Switch**: `Ctrl+Shift+Alt+Q`
> This hotkey immediately terminates B and releases all system hooks. Use this if B becomes over-eager or if you need an instant exit.

Because B lives on a transparent, click-through overlay without a standard close button, the kill switch is the only way to exit. It is registered at the Win32 level and works even if the Qt event loop is unresponsive.

---

## Technical Constraints

| Constraint    | Target                                              |
| :------------ | :-------------------------------------------------- |
| **CPU**       | Intel i5 (Quad-Core) or equivalent                  |
| **RAM**       | 16 GB                                               |
| **Display**   | Any resolution (adaptive)                           |
| **OS**        | Windows 10/11 (primary), Linux/macOS (experimental) |
| **Tick Rate** | 60 fps (16ms interval)                              |
| **Inference** | Groq API (cloud) or llama-cpp (local, 4GB+ model)   |

---

<div align="center">

Built by [Ahmad Hassan](https://github.com/Ahmad-Hassan-0)

*Wiring the soul, one tick at a time.*

</div>