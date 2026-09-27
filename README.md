# Agentic AI Application (Phase 5 — React Demo UI & Autonomous Engine)

A modular, hackathon-grade Agentic AI application built with **React**, **FastAPI**, **SQLite**, and **Pydantic**, designed to orchestrate tool-using autonomous workflows powered by LLM inference (Hugging Face Inference API).

---

## 🏛️ Architecture Overview

The agent executes multi-step goals through an explicit planning and observation loop:

```text
USER GOAL
   │
   ▼
[THINK/DECISION] (Concise plan: e.g. "weather information required for Chennai")
   │
   ▼
[TOOL_CALL] (weather, city="Chennai", call_id="call_a1b2c3d4")
   │
   ▼
[TOOL_RESULT] (32°C, Humid)
   │
   ▼
[THINK/DECISION] (Concise plan: e.g. "calculation required: 32 > 30")
   │
   ▼
[TOOL_CALL] (calculator, expression="32 > 30", call_id="call_e5f6g7h8")
   │
   ▼
[TOOL_RESULT] (True)
   │
   ▼
[FINAL] ("The current temperature in Chennai is 32°C, which is above 30°C.")
```

### Directory Structure

```text
agentic-ai/
├── app/
│   ├── __init__.py
│   ├── main.py          # FastAPI application & REST endpoints
│   ├── config.py        # Pydantic Settings & SecretStr masking
│   ├── llm/             # LLM provider abstraction & Hugging Face integration
│   │   ├── __init__.py
│   │   ├── base.py      # Abstract LLMClient, LLMMessage, LLMResponse, custom exceptions
│   │   └── huggingface.py# HuggingFaceLLMClient with function calling & router support
│   ├── agent/
│   │   ├── __init__.py
│   │   └── agent.py     # Multi-step Agent orchestrator & state tracker
│   ├── tools/           # Extensible tool subsystem
│   │   ├── __init__.py
│   │   └── registry.py  # BaseTool, Pydantic input schemas, AST calculator, weather, search
│   ├── models/
│   │   ├── __init__.py
│   │   └── schemas.py   # Data contracts: StepType, AgentStep, UserRequest, ToolCall, ToolResult, AgentResponse, AgentState
│   └── db/
│       ├── __init__.py
│       └── database.py  # SQLite connection management & session/audit trail persistence
├── tests/               # 50 unit and integration tests (100% mocked offline)
├── .env.example         # Environment template
├── .gitignore           # Git ignore rules for virtualenv, db, & secrets
├── requirements.txt     # Locked dependencies
└── README.md            # Project documentation
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

## 🖥️ Running the Demo UI (Phase 5)

The application features a modern React UI with live chat, prompt presets, and a real-time **Tool Execution Timeline** displaying decisions, tool dispatches, observations, and final synthesized answers.

### Option A: Run Fullstack via FastAPI (Recommended)
FastAPI automatically serves the built React frontend at port 8000:
```powershell
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```
Open **[http://localhost:8000](http://localhost:8000)** in your browser.

### Option B: Run Frontend with Vite Dev Server (Hot Reloading)
```powershell
# In a separate terminal:
cd frontend
npm install
npm run dev
```
Open **[http://localhost:5173](http://localhost:5173)** (Vite proxies requests to the FastAPI backend).

### UI Features
1. **Interactive Chat Stream**: Input goals, view conversational turns, and inspect execution durations.
2. **Preset Demo Buttons**: One-click demo triggers for multi-step weather comparisons, arithmetic, and search.
3. **Visual Execution Stepper**: Vertical timeline mapping `USER REQUEST` → `AGENT DECISION` → `TOOL_CALL` → `TOOL_RESULT` → `AGENT DECISION` → `FINAL ANSWER`.
4. **State JSON Inspector**: Toggle to inspect the raw `AgentState` payload with unique `run_id`, `call_id`, and ISO timestamps.
5. **Privacy Shield**: Never renders private internal hidden chain-of-thought; displays concise decision metadata only.

---

## 🧪 Running the Test Suite

Run the full pytest suite:
```powershell
pytest -v
```

The **60 automated tests** include:
- **Multi-step Tasks**: Weather + calculation, Search + calculation, Search + weather.
- **Error Recovery**: Automatic recovery when tool calls fail with invalid inputs.
- **Guardrails**: Maximum 8 tool calls per run, maximum iterations limit, and execution timeouts.
- **Step Categorization**: Verification of `THINK/DECISION`, `TOOL_CALL`, `TOOL_RESULT`, and `FINAL` step logs.
- **AST Safety**: Validates arithmetic and comparison expressions while strictly barring arbitrary code execution.
- **Persistence**: SQLite session state and audit trail logging.

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

