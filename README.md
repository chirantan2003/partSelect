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

## 💬 Comprehensive Testing Prompts

Use these 12 distinct prompt scenarios during a demonstration to verify every subsystem, agent, and component in the application.

### Category 1: Product & Model Catalog Lookup

1. **Part Details & Stock Inquiry**
   * **Prompt:** `Can you tell me the price and stock status of part number PS10065979?`
   * **Route:** `catalog_agent` ➔ `lookup_part(identifier="PS10065979")`
   * **UI Render:** `ProductCard` (Upper Rack Adjuster Kit, $55.29, In Stock).

2. **Model Identification**
   * **Prompt:** `What kind of appliance is model number WDT780SAEM1?`
   * **Route:** `catalog_agent` ➔ `lookup_model(model_number="WDT780SAEM1")`
   * **UI Render:** Text explaining the model is a Whirlpool dishwasher.

---

### Category 2: Compatibility Engine (Deterministic SQL)

3. **Compatible Check (Valid Relationship)**
   * **Prompt:** `Is part number PS10065979 compatible with my WDT780SAEM1 dishwasher?`
   * **Route:** `catalog_agent` ➔ `check_compatibility(ps_number="PS10065979", model_number="WDT780SAEM1")`
   * **UI Render:** Green check mark `CompatVerdict` card (Compatible).

4. **Incompatible Check (Appliance Type Mismatch Trap)**
   * **Prompt:** `Is the refrigerator door bin PS11752778 compatible with my WDT780SAEM1 dishwasher?`
   * **Route:** `catalog_agent` ➔ `check_compatibility(ps_number="PS11752778", model_number="WDT780SAEM1")`
   * **UI Render:** Red warning `CompatVerdict` card detailing: *"This is a refrigerator part, but your model WDT780SAEM1 is a dishwasher."*

5. **Incompatible Check (Same Appliance Type, No Matrix Link)**
   * **Prompt:** `Is the refrigerator ice maker PS2121513 compatible with my WRS322FDAM00 refrigerator?`
   * **Route:** `catalog_agent` ➔ `check_compatibility(ps_number="PS2121513", model_number="WRS322FDAM00")`
   * **UI Render:** Red `CompatVerdict` card (Incompatible).

---

### Category 3: Troubleshooting & Diagnosis (Hybrid RAG)

6. **Exact Token Symptom Search**
   * **Prompt:** `My refrigerator is Leaking. What parts do I need to inspect?`
   * **Route:** `repair_agent` ➔ `find_parts_by_symptom` (exact match on "Leaking") + `search_repair_guides`
   * **UI Render:** Diagnosis instructions alongside recommended replacement part cards.

7. **Colloquial Phrase Symptom Search**
   * **Prompt:** `dishwasher water wont leave`
   * **Route:** `repair_agent` ➔ `find_parts_by_symptom` (pgvector cosine similarity match to "Not Draining") + `search_repair_guides`
   * **UI Render:** Recommendations for drain pumps and filter checks.

---

### Category 4: Installation Instructions

8. **Guided Repair Accordion**
   * **Prompt:** `How do I install the dishwasher heating element PS8260087?`
   * **Route:** `catalog_agent` ➔ `get_installation_info(ps_number="PS8260087")`
   * **UI Render:** `InstallDrawer` accordion widget showing numbered installation steps, estimated times, and video link.

---

### Category 5: Cart & Order Operations

9. **Add to Shopping Cart**
   * **Prompt:** `Please add dishwasher rack track stop PS11746591 to my cart.`
   * **Route:** `order_agent` ➔ `add_to_cart(ps_number="PS11746591", qty=1)`
   * **UI Render:** Text updating cart status and showing items added.

10. **Order Status & Tracking**
    * **Prompt:** `Can you check the tracking status of my order ORD-10293?`
    * **Route:** `order_agent` ➔ `get_order_status(order_id="ORD-10293")`
    * **UI Render:** Order status details (Shipped) and direct link to the tracking page.

---

### Category 6: Multi-Turn Context & Guardrails

11. **Context-Aware Pronoun Resolution (Multi-Turn)**
    * **Prompt Sequence:**
      * Turn 1: `Show me details for part PS334230.` (Catalog specialist loads the part details card)
      * Turn 2: `Is it compatible with my WDF520PADM model?`
    * **Route:** Orchestrator context propagation passes chat history via `ContextVar` to the catalog executor, allowing it to automatically map `"it"` to `PS334230` during the compatibility matrix query.
    * **UI Render:** Red/Green `CompatVerdict` card.

12. **Out-of-Scope Pre-Routing Refusal**
    * **Prompts:** `How do I change the oil in my Honda Civic?` or `My laundry washing machine won't spin.`
    * **Route:** Pre-orchestrator `is_in_scope` guardrail (Gemini Flash-Lite) returns `False` inside 300ms.
    * **UI Render:** Polite refusal detailing that the assistant only supports refrigerators and dishwashers.

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
