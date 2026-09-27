# Agentic AI Studio & AG02 Transactional Engine

A unified, production-grade Agentic AI application built with **React**, **FastAPI**, **SQLite**, and **Pydantic**. Combines:
1. **Autonomous Agentic Loop**: Multi-step planning, tool orchestration, and real-time reflection powered by LLM inference (Hugging Face Inference API / Llama 3.1).
2. **AG02 Transactional Saga Engine**: Saga-pattern deterministic execution with reversible actions, crash recovery, human-in-the-loop approval gating, failure injection, an interactive 9-section control plane dashboard (`/dashboard`), and a manual **Undo Button**.

---

## 🏛️ Architecture Overview

The system features two complementary execution engines sharing a unified FastAPI backend:

```text
                                  ┌─────────────────────────────┐
                                  │      Agentic AI Studio      │
                                  └──────────────┬──────────────┘
                                                 │
                        ┌────────────────────────┴────────────────────────┐
                        ▼                                                 ▼
        ┌───────────────────────────────┐                 ┌───────────────────────────────┐
        │     Autonomous Agent Loop     │                 │   AG02 Saga Transactional     │
        │       (ReAct Planning)        │                 │            Engine             │
        ├───────────────────────────────┤                 ├───────────────────────────────┤
        │ • Multi-step Goal Planning    │                 │ • Forward Actions             │
        │ • AST Calculator              │                 │   (book_flight, reserve_hotel,│
        │ • OpenWeatherMap / Search     │                 │    charge_payment)            │
        │ • Browser Window Launching    │                 │ • Exact Compensations         │
        │ • Guardrails & Token Budgets  │                 │ • Approval Gates (Email)      │
        │ • Realtime Stepper Timeline   │                 │ • Crash Recovery & Replay     │
        │ • React Sage/Mint Theme UI    │                 │ • Manual Undo Button          │
        └───────────────────────────────┘                 │ • 9-Section Control Plane UI  │
                                                          └───────────────────────────────┘
```

### Directory Structure

```text
agentic-ai/
├── app/
│   ├── __init__.py
│   ├── main.py                 # FastAPI application, REST endpoints & static frontend mount
│   ├── config.py               # Pydantic Settings & SecretStr masking
│   ├── api/                    # AG02 REST & Dashboard API
│   │   ├── dashboard.py        # 9-section AG02 interactive HTML control plane
│   │   └── routes.py           # /ag02 endpoints (run, undo, inject-fault, approve, crash)
│   ├── core/                   # AG02 Transactional Engine modules
│   │   ├── agent.py            # Plan execution & compensation runner
│   │   ├── transaction_manager.py # Saga transaction state machine
│   │   ├── compensation_manager.py# Reverse compensation orchestrator
│   │   ├── durable_log.py      # SQLite durable transaction log
│   │   ├── recovery_manager.py # Crash recovery & transaction replay
│   │   ├── approval_manager.py # Human-in-the-loop approval gates
│   │   ├── fault_injector.py   # Step & compensation failure injection
│   │   ├── mock_world.py       # Deterministic world state tracker
│   │   ├── tool_registry.py    # Reversible tool definitions
│   │   └── llm_agent.py        # Structured plan generation
│   ├── agent/                  # Autonomous ReAct Agent orchestrator & state tracker
│   ├── tools/                  # Extensible tools (AST Calculator, Weather, Search, Browser)
│   ├── models/                 # Shared Pydantic data schemas
│   └── db/                     # SQLite connection management & audit trail
├── frontend/                   # React + Vite UI (Sage & Mint design palette)
├── tests/                      # 126 automated unit and integration tests
├── .env.example                # Environment template
├── Dockerfile                  # Multi-stage Docker container
├── render.yaml                 # 1-click Render blueprint
└── README.md                   # Project documentation
```

---

## 🧠 Multi-Step Planning & Execution Features (Phase 4)

1. **Explicit State Tracking (`AgentState`)**:
   Tracks `user_goal`, `current_step`, `tool_calls`, `tool_results`, `final_answer`, `status`, and step history.
2. **Unique Identifiers**:
   - `run_id`: Unique execution run identifier (e.g. `run_3f91a2bc`).
   - `call_id`: Unique identifier for each tool invocation (e.g. `call_8a12d93e`).
   - `step_id`: Unique identifier for every discrete step.
3. **Discrete Step Categorization**:
   - `THINK/DECISION`: Action planning without exposing private hidden reasoning.
   - `TOOL_CALL`: Dispatched tool and arguments.
   - `TOOL_RESULT`: Tool execution outcome.
   - `FINAL`: Final answer to the user.
4. **No Hidden Chain-of-Thought Exposure**:
   Stores only concise decision metadata such as `"weather information required for Chennai"` or `"calculation required: 32 > 30"` rather than raw private reasoning traces.
5. **Robust Guardrails**:
   - **Maximum 8 tool calls per run**: Strict budget prevents runaway multi-tool loops.
   - **Maximum loop iterations**: Configurable cutoff (`max_iterations = 10`).
   - **Timeout protection**: Hard wall-clock execution deadline (`timeout_seconds = 60s`).
   - **AST-based safe calculator**: Completely blocks arbitrary code execution.
   - **Pydantic argument validation**: Arguments are typed and validated before execution.
   - **Error recovery**: Failed tool calls return informative observations to the LLM, enabling self-correction.

---

## 📋 Example Execution Trace

### Request
`POST /agent/run`
```json
{
  "query": "Find the weather in Chennai and calculate whether the temperature is above 30°C."
}
```

### Complete Response Trace
```json
{
  "run_id": "run_a8f9c120",
  "session_id": "sess_412e8b93",
  "status": "completed",
  "response": "The current temperature in Chennai is 32°C, which is above 30°C.",
  "iterations": 3,
  "steps": [
    {
      "step_id": "step_01",
      "step_type": "THINK/DECISION",
      "content": "weather information required for Chennai",
      "timestamp": "2026-09-26T17:40:01.120Z",
      "metadata": {"tool_name": "weather", "call_id": "call_w01"}
    },
    {
      "step_id": "step_02",
      "step_type": "TOOL_CALL",
      "content": "Calling weather with {\"city\": \"Chennai\"}",
      "timestamp": "2026-09-26T17:40:01.125Z",
      "metadata": {"call_id": "call_w01", "arguments": {"city": "Chennai"}}
    },
    {
      "step_id": "step_03",
      "step_type": "TOOL_RESULT",
      "content": "{\"city\": \"Chennai\", \"temperature\": \"32°C\", \"condition\": \"Humid and partly cloudy\", \"humidity\": \"78%\"}",
      "timestamp": "2026-09-26T17:40:01.130Z",
      "metadata": {"call_id": "call_w01", "success": true}
    },
    {
      "step_id": "step_04",
      "step_type": "THINK/DECISION",
      "content": "calculation required: 32 > 30",
      "timestamp": "2026-09-26T17:40:01.850Z",
      "metadata": {"tool_name": "calculator", "call_id": "call_c02"}
    },
    {
      "step_id": "step_05",
      "step_type": "TOOL_CALL",
      "content": "Calling calculator with {\"expression\": \"32 > 30\"}",
      "timestamp": "2026-09-26T17:40:01.855Z",
      "metadata": {"call_id": "call_c02", "arguments": {"expression": "32 > 30"}}
    },
    {
      "step_id": "step_06",
      "step_type": "TOOL_RESULT",
      "content": "True",
      "timestamp": "2026-09-26T17:40:01.860Z",
      "metadata": {"call_id": "call_c02", "success": true}
    },
    {
      "step_id": "step_07",
      "step_type": "FINAL",
      "content": "The current temperature in Chennai is 32°C, which is above 30°C.",
      "timestamp": "2026-09-26T17:40:02.500Z",
      "metadata": {"iterations": 3}
    }
  ],
  "tool_calls": [
    {
      "call_id": "call_w01",
      "tool_name": "weather",
      "arguments": {"city": "Chennai"}
    },
    {
      "call_id": "call_c02",
      "tool_name": "calculator",
      "arguments": {"expression": "32 > 30"}
    }
  ],
  "tool_results": [
    {
      "call_id": "call_w01",
      "tool_name": "weather",
      "output": {"city": "Chennai", "temperature": "32°C", "condition": "Humid and partly cloudy", "humidity": "78%"},
      "success": true,
      "error": null
    },
    {
      "call_id": "call_c02",
      "tool_name": "calculator",
      "output": true,
      "success": true,
      "error": null
    }
  ]
}
```

---

## 🚀 Getting Started

### 1. Prerequisites
- Python 3.11 or higher
- Git (optional)

### 2. Create Virtual Environment

On Windows (PowerShell):
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

On Linux/macOS:
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install Dependencies
```powershell
pip install -r requirements.txt
```

### 4. Configure Environment
Copy `.env.example` to `.env`:
```powershell
Copy-Item .env.example .env
```

Configure parameters in `.env`:
```env
APP_ENV=development
LOG_LEVEL=INFO
API_HOST=0.0.0.0
API_PORT=8000
DATABASE_PATH=agentic_ai.db

# Hugging Face Inference API Configuration
HF_TOKEN=your_hugging_face_token_here
HF_MODEL=meta-llama/Meta-Llama-3-8B-Instruct
HF_BASE_URL=https://api-inference.huggingface.co/v1
LLM_TIMEOUT=30.0
```

---

## 🖥️ Running the User Interfaces

### 1. Agentic AI Studio (React Frontend)
FastAPI automatically serves the built React frontend at port 8000:
```powershell
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```
Open **[http://localhost:8000](http://localhost:8000)** in your browser.
- **Interactive Chat Stream**: Input goals, view conversational turns, and inspect execution durations.
- **Preset Demo Buttons**: One-click demo triggers for multi-step weather comparisons, arithmetic, browser tab opening, and search.
- **Visual Execution Stepper**: Vertical timeline mapping `USER REQUEST` → `AGENT DECISION` → `TOOL_CALL` → `TOOL_RESULT` → `FINAL ANSWER`.
- **Sage & Mint Palette**: High-contrast, accessibility-tested dark theme with smooth glassmorphism.
- **Undo & Saga Link**: Direct header access to the AG02 Control Plane dashboard.

### 2. AG02 Saga & Undo Control Plane Dashboard
Open **[http://localhost:8000/dashboard](http://localhost:8000/dashboard)** (or `/ag02/`).
The AG02 control plane provides 9 real-time visual sections:
1. **System Status & Health**: Live runtime metrics, active database, crash recovery state.
2. **Current World State**: Live tracking of Flight Bookings, Hotel Reservations, Payment ledger, and Tickets.
3. **Interactive Plan Runner**: Execute travel bookings (`book_flight`, `reserve_hotel`, `charge_payment`, `issue_ticket`).
4. **The Undo Button**: Instantly triggers reverse compensation (`refund_payment`, `cancel_hotel`, `cancel_flight`) to restore the deterministic mock world back to its original state.
5. **Approval Interlock**: Gated execution for irreversible actions (e.g. `send_confirmation_email`), requiring user approval before dispatch.
6. **Failure & Fault Injection Matrix**: Injects failures at any step (e.g., Step 3 Payment failure) to demonstrate automatic reverse rollback.
7. **Crash Simulator**: Simulates mid-transaction node crashes and tests automated recovery on reboot.
8. **Durable SQLite Transaction Log**: Real-time audit log of transaction IDs, step states, and encrypted payloads.
9. **Event Stream Log**: Live inspection of state transitions and compensation events.

---

## 🧪 Running the Test Suite

Run the full pytest suite:
```powershell
pytest -v
```

The **126 automated tests** cover:
- **Autonomous Agent**: Multi-step ReAct planning, AST safety, calculator comparisons, weather, search, browser automation, and token budgets (60 tests).
- **AG02 Saga Engine**: Deterministic mock world, forward tools, exact compensations, failure matrices, durable SQLite logs, crash recovery, approval gates, and dashboard endpoints (66 tests).

---

## ☁️ Deploying to Render (Free Cloud Hosting)

This project is fully optimized for **[Render](https://render.com/)** with zero-configuration Docker and Blueprint support.

### Option 1: 1-Click Render Blueprint (Recommended)

1. Go to your **[Render Dashboard](https://dashboard.render.com/)**.
2. Click **New +** → **Blueprint**.
3. Connect your repository: `VK-JPG-cmd/Agentic-AI-Studio`.
4. Render will read `render.yaml` automatically.
5. In the environment setup, provide your **`HF_TOKEN`** (from [Hugging Face Settings](https://huggingface.co/settings/tokens)).
6. Click **Apply**. Your app will build and deploy on Render's free tier with automated health checks!

### Option 2: Manual Web Service Setup (Docker)

1. On Render, click **New +** → **Web Service**.
2. Connect your GitHub repository.
3. Choose **Docker** as the Runtime.
4. Set the Environment Variables:
   - `APP_ENV`: `production`
   - `HF_TOKEN`: `<your_hugging_face_token>`
   - `HF_MODEL_ID`: `meta-llama/Llama-3.1-8B-Instruct`
   - `HF_BASE_URL`: `https://router.huggingface.co/v1`
   - `LLM_FALLBACK_TO_MOCK`: `true`
5. Click **Create Web Service**. Render builds the multi-stage Docker image and serves both the FastAPI API and the React frontend on your assigned `https://<service-name>.onrender.com` URL.

