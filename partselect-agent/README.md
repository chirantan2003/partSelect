# 🔧 PartSelect Parts Assistant

An AI-powered chat agent for the [PartSelect](https://www.partselect.com) e-commerce platform, specializing in **refrigerator** and **dishwasher** parts. The agent helps users find parts, verify model compatibility, troubleshoot appliance problems, get installation guidance, and manage orders — all through a conversational interface.

![Stack](https://img.shields.io/badge/Next.js-Frontend-black?logo=next.js)
![Stack](https://img.shields.io/badge/FastAPI-Backend-009688?logo=fastapi)
![Stack](https://img.shields.io/badge/LangChain-Agents-1C3C3C?logo=langchain)
![Stack](https://img.shields.io/badge/Gemini_2.5_Flash-LLM-4285F4?logo=google)
![Stack](https://img.shields.io/badge/PostgreSQL+pgvector-Database-4169E1?logo=postgresql)

---

## 📋 Table of Contents

- [Architecture](#-architecture)
- [Key Design Decisions](#-key-design-decisions)
- [Tech Stack](#-tech-stack)
- [Project Structure](#-project-structure)
- [Getting Started](#-getting-started)
- [Running the App](#-running-the-app)
- [Testing](#-testing)
- [Example Queries](#-example-queries)
- [Extensibility](#-extensibility)

---

## 🏗 Architecture

```
┌────────────────────────────────────────────────────────────┐
│  BROWSER  (Next.js + TypeScript + CSS Modules)             │
│  useAgentChat() → SSE stream → rich UI cards               │
└──────────▲─────────────────────────────────┬───────────────┘
           │  SSE events                      │  POST /chat
           │                                  ▼
┌──────────┴─────────────────────────────────────────────────┐
│  FASTAPI  (Python)                                          │
│                                                              │
│  Guardrail (Flash-Lite) ── OUT ──► polite refusal           │
│      │ IN_SCOPE                                              │
│      ▼                                                       │
│  Orchestrator AgentExecutor (Gemini 2.5 Flash)              │
│    tools = [catalog_agent, repair_agent, order_agent]        │
│        ┌───────────┼───────────┐                             │
│        ▼           ▼           ▼                             │
│   CatalogAgent  RepairAgent  OrderAgent                      │
│   4 tools       3 tools      3 tools                         │
│        └───────────┴───────────┘                             │
│                    │                                          │
│                    ▼                                          │
│  Supabase (PostgreSQL + pgvector)                            │
└────────────────────────────────────────────────────────────┘
```

The system uses an **agents-as-tools** pattern: three specialist `AgentExecutor` instances are wrapped as LangChain `Tool` objects and orchestrated by a top-level router agent. Each specialist has a focused system prompt and a curated subset of tools.

---

## 🎯 Key Design Decisions

### The Trap Query
Example query #2 (`Is PS11752778 compatible with WDT780SAEM1?`) is a **deliberate trap**:
- **PS11752778** is a _Refrigerator Door Shelf Bin_
- **WDT780SAEM1** is a _Whirlpool Dishwasher_

The correct answer is **No** — a refrigerator part cannot fit a dishwasher. This is why **compatibility is always a deterministic SQL JOIN**, never a vector similarity search. Embeddings would hallucinate a plausible "yes" since both are Whirlpool products.

### Why Not LangGraph?
LangGraph's strengths (durable checkpoints, deterministic routing edges, human-in-the-loop gates) solve problems this project doesn't have. All queries are single-turn or short-sequence interactions. LangGraph is documented as the **upgrade path** for when flows need resumable multi-session diagnosis or compliance approval.

---

## ⚙ Tech Stack

| Layer | Technology | Why |
|---|---|---|
| **Frontend** | Next.js 15 + TypeScript + CSS Modules | SSE streaming UI, brand-matched styling |
| **API** | FastAPI + SSE streaming | Serves the agent to any client |
| **Orchestration** | LangChain `AgentExecutor` + agents-as-tools | Provider-agnostic, clean tool-calling loop |
| **LLM** | Gemini 2.5 Flash + Flash-Lite | Free tier via AI Studio |
| **Database** | Supabase PostgreSQL + pgvector | Relational truth + semantic search in one DB |
| **Scraper** | httpx + BeautifulSoup4 | Data acquisition from PartSelect pages |
| **Eval** | pytest + pytest-asyncio | Terminal-runnable test suite |

---

## 📁 Project Structure

```
partselect-agent/
├── backend/
│   ├── main.py                    # FastAPI app + SSE /chat endpoint
│   ├── llm.py                     # LangChain LLM setup (Gemini Flash + Flash-Lite)
│   ├── db.py                      # asyncpg connection pool
│   ├── embeddings.py              # Gemini Embedding-2 (768-dim)
│   ├── guardrail.py               # Scope classifier (IN/OUT)
│   ├── memory.py                  # Session persistence (PostgreSQL)
│   ├── prompts.py                 # All system prompts
│   ├── agents/
│   │   ├── specialists.py         # 3 AgentExecutors + agent-tool wrappers
│   │   └── orchestrator.py        # Top-level routing agent
│   ├── tools/
│   │   ├── catalog_tools.py       # lookup_part, check_compatibility,
│   │   │                          # get_installation_info, find_parts_by_symptom
│   │   ├── repair_tools.py        # search_repair_guides
│   │   └── order_tools.py         # add_to_cart, get_order_status
│   └── scraper/
│       ├── extract.py             # HTML parsing
│       └── load.py                # Upsert to database
├── frontend/
│   ├── src/
│   │   ├── app/
│   │   │   ├── globals.css        # Design tokens (PartSelect brand colors)
│   │   │   ├── page.tsx           # Entry point
│   │   │   └── components/
│   │   │       ├── Chat.tsx       # Main chat container
│   │   │       ├── ProductCard.tsx # Part details card
│   │   │       ├── CompatVerdict.tsx # Green/Red compatibility verdict
│   │   │       ├── InstallDrawer.tsx  # Accordion install steps
│   │   │       ├── Composer.tsx   # Text input + send button
│   │   │       ├── SuggestedPrompts.tsx # Quick-action chips
│   │   │       └── *.module.css   # Component CSS Modules
│   │   └── hooks/
│   │       └── useAgentChat.ts    # Custom SSE streaming hook
│   └── package.json
├── db/
│   └── schema.sql                 # Full database schema
├── eval/
│   └── test_suite.py              # pytest assertions
├── test_cli.py                    # Individual tool tests
├── test_agents.py                 # Agent-level tests
├── test_orchestrator.py           # Full flow tests
├── .env                           # API keys + DATABASE_URL
└── docker-compose.yml
```

---

## 🚀 Getting Started

### Prerequisites

- **Python 3.11+**
- **Node.js 18+** and **npm**
- **PostgreSQL** with the `pgvector` extension (or Supabase)
- **Gemini API Key** from [Google AI Studio](https://aistudio.google.com)

### 1. Clone & Setup Environment

```bash
cd partselect-agent
python -m venv venv

# Windows
.\venv\Scripts\activate

# macOS/Linux
source venv/bin/activate
```

### 2. Install Python Dependencies

```bash
pip install langchain langchain-google-genai langchain-core
pip install fastapi uvicorn asyncpg
pip install google-genai
pip install httpx beautifulsoup4
pip install pydantic python-dotenv
pip install pytest pytest-asyncio
```

### 3. Configure Environment Variables

Create a `.env` file in the project root:

```env
GEMINI_API_KEY=your-ai-studio-key
DATABASE_URL=postgresql://user:pass@host:5432/dbname
```

### 4. Initialize Database

```bash
psql "$DATABASE_URL" -f db/schema.sql
```

### 5. Seed Data

```bash
python -m backend.scraper.seed
```

### 6. Install Frontend Dependencies

```bash
cd frontend
npm install
cd ..
```

---

## ▶ Running the App

### Start the Backend (Terminal 1)

```bash
cd partselect-agent
.\venv\Scripts\python -m backend.main
# Server starts on http://localhost:8000
```

### Start the Frontend (Terminal 2)

```bash
cd partselect-agent/frontend
npm run dev
# App opens at http://localhost:3000
```

Open [http://localhost:3000](http://localhost:3000) in your browser.

---

## 🧪 Testing

Tests are designed to be run **before** the frontend, verifying correctness at each layer:

```bash
# 1. Test individual tools
python -m test_cli

# 2. Test individual agents
python -m test_agents

# 3. Test full orchestrator flow
python -m test_orchestrator

# 4. Run pytest evaluation suite
pytest eval/ -v
```

### What the Tests Verify

| Test | Validates |
|---|---|
| `test_cli.py` | `lookup_part` returns correct data; `check_compatibility` catches the trap query |
| `test_agents.py` | Catalog agent calls right tools; correctly identifies cross-appliance mismatch |
| `test_orchestrator.py` | Full routing works; guardrail blocks off-topic; all 3 example queries pass |
| `eval/test_suite.py` | Compatibility true/false, part not found, guardrail IN/OUT assertions |

---

## 💬 Example Queries

### Query 1 — Installation
> "How can I install part number PS11752778?"

**Expected:** `lookup_part` → `ProductCard` · `get_installation_info` → `InstallDrawer` with steps, difficulty, and video link.

### Query 2 — Compatibility (⚠️ Trap)
> "Is PS11752778 compatible with my WDT780SAEM1 model?"

**Expected:** `check_compatibility` → **Red** `CompatVerdict` card: "This is a refrigerator part, but WDT780SAEM1 is a dishwasher."

### Query 3 — Troubleshooting
> "The ice maker on my Whirlpool fridge is not working. How can I fix it?"

**Expected:** `search_repair_guides` → `find_parts_by_symptom` → `lookup_part` → Diagnosis + `ProductCard` recommendations.

---

## 🔌 Extensibility

| What to Change | How | Effort |
|---|---|---|
| **Swap LLM provider** | Change 1 line in `llm.py` (`ChatGoogleGenerativeAI` → `ChatOpenAI`) | 1 line |
| **Add appliance type** | Add to DB `CHECK` constraint + seed data | Minimal |
| **Add new tool** | Write a `@tool` function, add to an agent's tool list | 1 file |
| **Add new agent** | Write an `AgentExecutor`, wrap as `Tool`, append to orchestrator | 1 file |
| **Scale to production** | gunicorn workers + asyncpg pool sizing | Config only |
| **Migrate to LangGraph** | Replace `AgentExecutor` with `StateGraph`; tools & prompts carry over | Moderate |

---

## 📄 License

This project was built as a case study for PartSelect.
