# Backend — PartSelect Parts Assistant

The Python backend powering the PartSelect chat agent. Built with **FastAPI** and **LangChain**, it exposes a single streaming `/chat` endpoint that orchestrates a multi-agent pipeline backed by PostgreSQL + pgvector.

---

## Directory Structure

```
backend/
├── main.py            # FastAPI app + SSE /chat endpoint
├── llm.py             # LangChain LLM configuration
├── db.py              # asyncpg connection pool
├── embeddings.py      # Gemini Embedding-2 helper (768-dim)
├── guardrail.py       # Scope classifier (IN / OUT)
├── memory.py          # Session persistence (PostgreSQL)
├── prompts.py         # All system prompts
├── agents/
│   ├── specialists.py # 3 AgentExecutors + agent-tool wrappers
│   └── orchestrator.py# Top-level routing agent
├── tools/
│   ├── catalog_tools.py  # lookup_part, check_compatibility,
│   │                     # get_installation_info, find_parts_by_symptom
│   ├── repair_tools.py   # search_repair_guides
│   └── order_tools.py    # add_to_cart, get_order_status
└── scraper/
    ├── config.py      # All scraper constants, URLs, CSS selectors
    ├── parts_scraper.py  # Scrapes part listing + detail pages
    ├── repair_scraper.py # Scrapes symptom/repair guide pages
    ├── blog_scraper.py   # Scrapes blog articles
    ├── extract.py     # HTML parsing helpers
    ├── load.py        # DB upsert logic
    ├── seed.py        # Master seeding script (run this to populate DB)
    └── utils.py       # Shared utilities (rate limiting, retries, etc.)
```

---

## Module Reference

### `main.py` — API Entry Point

The FastAPI application. Exposes one endpoint:

| Method | Path | Description |
|---|---|---|
| `POST` | `/chat` | Accepts `messages[]` + `session_id`, returns an SSE stream |

**Request body:**
```json
{
  "messages": [
    { "role": "user", "content": "Is PS11752778 compatible with WDT780SAEM1?" }
  ],
  "session_id": "abc-123"
}
```

**SSE event stream (response):**

| Event | Payload | When |
|---|---|---|
| `tool_status` | `{ state, message }` | Immediately after scope check passes |
| `tool_result` | `{ tool_name, args, result }` | After each specialist tool call |
| `text` | `string` | Final natural-language response |
| `done` | `{ agent }` | Stream complete |

**Request lifecycle:**
```
POST /chat
  │
  ├── 1. Extract last user message + build context (last 6 messages)
  ├── 2. Guardrail (is_in_scope) — refusal if OUT
  ├── 3. run_orchestrator(message, context)
  │       └── routes to catalog_agent / repair_agent / order_agent
  ├── 4. SSE: yield tool_result events (one per tool call)
  ├── 5. SSE: yield text event
  ├── 6. SSE: yield done event
  └── 7. Persist session to PostgreSQL
```

---

### `llm.py` — LLM Configuration

Two model instances using `langchain-google-genai`:

| Instance | Model | Temperature | Used By |
|---|---|---|---|
| `flash` | `gemini-2.5-flash` | 0.2 | Orchestrator + all specialist agents |
| `flash_lite` | `gemini-2.5-flash-lite` | 0 | Guardrail classifier only |

To swap providers, replace one line:
```python
# from langchain_openai import ChatOpenAI
# flash = ChatOpenAI(model="gpt-4o")
```

---

### `db.py` — Database Connection Pool

Manages a global `asyncpg` connection pool. The pool is created once at startup and reused across all requests.

```python
pool = await get_pool()
async with pool.acquire() as conn:
    row = await conn.fetchrow("SELECT ...")
```

---

### `embeddings.py` — Vector Embeddings

Generates 768-dimensional embeddings using `gemini-embedding-2` via the Google GenAI SDK. Used for semantic search over symptoms and repair guides.

```python
vec = await embed("ice maker not making ice")
# Returns list[float] of length 768
```

---

### `guardrail.py` — Scope Classifier

A cheap binary classifier that runs **before** any agent or DB call. Uses `flash_lite` to classify the query as `IN` (refrigerator/dishwasher parts, compatibility, installation, orders) or `OUT` (everything else).

- **Fail-open:** If the classifier API call fails, defaults to `True` (in-scope) to avoid blocking valid users on transient errors.
- Classifiers that return `OUT` receive a polite refusal without touching the agent pipeline.

---

### `memory.py` — Session Persistence

Loads and saves conversation history to the `chat_sessions` table in PostgreSQL (JSONB column). The last 6 messages are passed as context to the orchestrator on every request.

---

### `prompts.py` — System Prompts

Contains all system prompts in one file for easy maintenance:

| Constant | Agent |
|---|---|
| `ORCHESTRATOR_PROMPT` | Top-level router — routing rules, synthesis instructions |
| `CATALOG_AGENT_PROMPT` | Part lookups, compatibility, installation |
| `REPAIR_AGENT_PROMPT` | Troubleshooting, diagnosis, symptom-to-part matching |
| `ORDER_AGENT_PROMPT` | Cart operations, order status |

---

## Agents

### `agents/specialists.py` — The Three Specialist Agents

Each specialist is a LangChain `AgentExecutor` with a focused tool subset and its own system prompt. Each executor is then **wrapped as a `Tool`** for the orchestrator.

| Agent Tool | Tools Available | Handles |
|---|---|---|
| `catalog_agent_tool` | `lookup_part`, `check_compatibility`, `get_installation_info`, `find_parts_by_symptom` | Part lookups, compatibility, install steps |
| `repair_agent_tool` | `search_repair_guides`, `find_parts_by_symptom`, `lookup_part` | Troubleshooting, symptom diagnosis |
| `order_agent_tool` | `add_to_cart`, `get_order_status`, `lookup_part` | Cart, order tracking |

### `agents/orchestrator.py` — The Router

A top-level `AgentExecutor` whose only tools are the three specialist agent tools. It receives the user's message, picks the right specialist, and synthesizes their response into a final conversational answer.

```python
result = await run_orchestrator(user_input, context)
# Returns: { "text": str, "tool_results": list[dict], "agent": str }
```

---

## Tools

### `tools/catalog_tools.py`

| Tool | Input | Returns |
|---|---|---|
| `lookup_part(identifier)` | PS number or MPN | Part details (price, stock, rating, description, image) |
| `check_compatibility(ps_number, model_number)` | PS# + model# | `{ compatible: bool, part_type, model_type }` |
| `get_installation_info(ps_number)` | PS# | Ordered steps, difficulty, estimated minutes, video URL |
| `find_parts_by_symptom(symptom, appliance_type)` | Symptom text + type | Exact + fuzzy matched parts |

> ⚠️ **`check_compatibility` is always a SQL JOIN** — never a vector search. This prevents hallucinating compatibility based on brand similarity (the "trap query").

### `tools/repair_tools.py`

| Tool | Input | Returns |
|---|---|---|
| `search_repair_guides(query, appliance_type, brand?)` | Query text + type | Top 3 semantically matched repair guides |

### `tools/order_tools.py`

| Tool | Input | Returns |
|---|---|---|
| `add_to_cart(ps_number, qty)` | PS# + quantity | Updated cart summary |
| `get_order_status(order_id)` | Order ID (e.g. `ORD-10293`) | Status + tracking URL |

---

## Scraper

The scraper populates the database from live PartSelect pages. Run `seed.py` once to seed the database for the case study.

### Files

| File | Purpose |
|---|---|
| `config.py` | All constants: URLs, CSS selectors, brand list, symptom slugs, rate limits |
| `parts_scraper.py` | Crawls facet search listing pages + individual part detail pages |
| `repair_scraper.py` | Crawls symptom/repair guide pages for all configured symptoms |
| `blog_scraper.py` | Crawls blog articles for repair guides and how-to content |
| `extract.py` | HTML parsing helpers (price, stock, ratings, compatible models, symptoms) |
| `load.py` | Upserts parsed data into the PostgreSQL schema |
| `utils.py` | Rate-limited HTTP client with exponential backoff + retry |
| `seed.py` | Master orchestration script — runs all scrapers, embeds, and loads data |

### Running the Scraper

```bash
# Seed the full database (parts + repair guides + blog articles)
python -m backend.scraper.seed

# Verify data was loaded
psql "$DATABASE_URL" -c "SELECT ps_number, name, price_cents FROM parts LIMIT 10;"
psql "$DATABASE_URL" -c "SELECT COUNT(*) FROM part_compatibility;"
psql "$DATABASE_URL" -c "SELECT description FROM symptoms LIMIT 5;"
```

### Scraper Configuration (`config.py`)

| Setting | Default | Description |
|---|---|---|
| `REQUEST_DELAY_MIN` | 2.0s | Min delay between requests |
| `REQUEST_DELAY_MAX` | 4.0s | Max delay between requests |
| `RETRY_ATTEMPTS` | 5 | Max retries on failure |
| `RETRY_BACKOFF_BASE` | 3 | Exponential backoff base (3^n seconds) |
| `MAX_PAGES_PER_CATEGORY` | 0 (unlimited) | Cap pagination; set to `N` to limit |
| `CHECKPOINT_INTERVAL` | 50 | Save progress every N parts |
| `APPLIANCE_TYPES` | `["Refrigerator", "Dishwasher"]` | Categories to scrape |

---

## Running the Backend

```bash
# From the partselect-agent/ root with venv activated:
python -m backend.main
# → Uvicorn starts on http://localhost:8000

# Or with hot-reload:
uvicorn backend.main:app --reload --port 8000
```

### Testing the API directly

You can test the backend streaming API directly via `curl` payloads.

#### Scenario A: Single-Turn Compatibility Check (The Trap Query)
```bash
curl -N -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [
      { "role": "user", "content": "Is PS11752778 compatible with WDT780SAEM1?" }
    ],
    "session_id": "api-test-session"
  }'
```

#### Scenario B: Multi-Turn Conversation (Context & Pronoun Resolution)
To verify that the orchestrator propagates history via `ContextVar` to resolve pronouns, pass the message array representing the preceding turns:
```bash
curl -N -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [
      { "role": "user", "content": "Show me details for part PS334230." },
      { "role": "assistant", "content": "Here is the Door Catch Kit (PS334230) for $8.55..." },
      { "role": "user", "content": "Is it compatible with my WDF520PADM model?" }
    ],
    "session_id": "api-test-session"
  }'
```

#### Expected SSE Output Stream:
```
event: tool_status
data: {"state": "routing", "message": "Finding the right specialist..."}

event: tool_result
data: {"tool_name": "check_compatibility", "args": {"ps_number": "PS334230", "model_number": "WDF520PADM"}, "result": {"compatible": true, ...}}

event: text
data: "Yes, the Door Catch Kit (PS334230) is compatible with your WDF520PADM dishwasher..."

event: done
data: {"agent": "catalog_agent"}
```
