# PartSelect Chat Agent — Complete Implementation Guide

> **Build order: database → scraper → tools → agents → terminal test → API → frontend.**
> Everything is verified in the terminal before a single line of UI code is written.

---

## 0. Stack at a glance

| Layer | Choice | Why |
|---|---|---|
| Frontend | Next.js + TypeScript + CSS Modules | Streaming UI, brand-matched styling *(built last)* |
| API | FastAPI (Python) + SSE streaming | Serves the agent to any client |
| Orchestration | **LangChain** — `AgentExecutor` + agents-as-tools | Provider-agnostic LLM calls, clean tool-calling loop, swappable models |
| LLM | `langchain-google-genai` → Gemini 2.5 Flash + Flash-Lite | Free tier via AI Studio |
| Truth engine | Supabase (PostgreSQL + `pgvector`) via **asyncpg** | Relational truth + semantic search |
| Scraper | **httpx** + **BeautifulSoup4** | Data acquisition pipeline |
| Eval | **pytest** + pytest-asyncio | Terminal-runnable test suite |

**Why LangChain over the manual loop:** (1) swapping Gemini → OpenAI → Anthropic is a
one-line change with identical tool behavior, which directly demonstrates extensibility;
(2) `AgentExecutor` handles the call→result→loop cycle so we don't maintain our own;
(3) the agents-as-tools pattern (wrapping an `AgentExecutor` as a `Tool`) is a
well-documented, recognizable agentic architecture pattern.

---

## 1. Design principles

### 1.1 Language → Truth → UI
The model never invents facts. Every fact comes from the database via typed tools.

### 1.2 Deterministic vs. semantic — never confuse them
**The trap in query #2:** PS11752778 is a *Refrigerator Door Shelf Bin*. WDT780SAEM1 is a
*Whirlpool Dishwasher*. Compatibility is a SQL join, never an embedding.

### 1.3 Stay in scope, cheaply
Flash-Lite classify-first. Off-topic traffic never reaches the agents.

---

## 2. Architecture overview

```
┌──────────────────────────────────────────────────────────────────┐
│  BROWSER  (Next.js + TypeScript)              ◄── BUILT LAST     │
│  useAgentChat() → SSE → message state + tool-result cards        │
└──────────▲──────────────────────────────────────┬────────────────┘
           │  SSE events                           │  POST /chat
           │                                       ▼
┌──────────┴───────────────────────────────────────────────────────┐
│  FASTAPI  (Python)                             ◄── PHASE 4       │
│                                                                   │
│  guardrail ── OUT ──► refusal                                    │
│      │ IN_SCOPE                                                  │
│      ▼                                                           │
│  orchestrator AgentExecutor                    ◄── PHASE 2       │
│    tools = [catalog_agent_tool, repair_agent_tool, order_agent_tool]
│        ┌───────────┼───────────┐                                 │
│        ▼           ▼           ▼                                 │
│   CatalogAgent  RepairAgent  OrderAgent   (each an AgentExecutor)│
│   @tool funcs   @tool funcs  @tool funcs                         │
│        │           │           │                                 │
│        └───────────┴───────────┘                                 │
│                    │                                              │
│                    ▼                                              │
│  SUPABASE (PostgreSQL + pgvector)              ◄── PHASE 1       │
└──────────────────────────────────────────────────────────────────┘
```

---

# PHASE 1 — Foundation

## 3. Setup & environment

```bash
mkdir partselect-agent && cd partselect-agent
python -m venv venv && source venv/bin/activate

# Core deps
pip install langchain langchain-google-genai langchain-core
pip install fastapi uvicorn asyncpg
pip install google-genai            # for embeddings
pip install httpx beautifulsoup4    # scraper
pip install pydantic python-dotenv
pip install pytest pytest-asyncio   # eval

# .env
cat > .env << 'EOF'
GEMINI_API_KEY=your-ai-studio-key
DATABASE_URL=postgresql://user:pass@db.supabase.co:5432/postgres
EOF
```

## 4. Database schema

```sql
-- db/schema.sql
create extension if not exists vector;

create table appliance_models (
  id             bigserial primary key,
  model_number   text unique not null,
  brand          text not null,
  appliance_type text not null check (appliance_type in ('refrigerator','dishwasher'))
);

create table parts (
  id             bigserial primary key,
  ps_number      text unique not null,
  mpn            text,
  name           text not null,
  description    text,
  price_cents    int not null,
  in_stock       boolean not null default true,
  image_url      text,
  rating         numeric(2,1),
  review_count   int default 0,
  appliance_type text not null check (appliance_type in ('refrigerator','dishwasher')),
  video_url      text
);

create table part_cross_refs (
  part_id    bigint references parts(id),
  alt_number text not null,
  primary key (part_id, alt_number)
);
create index on part_cross_refs (alt_number);

create table part_compatibility (
  part_id  bigint references parts(id),
  model_id bigint references appliance_models(id),
  primary key (part_id, model_id)
);
create index on part_compatibility (model_id);

create table symptoms (
  id             bigserial primary key,
  description    text unique not null,
  appliance_type text not null,
  embedding      vector(768)
);
create index on symptoms using hnsw (embedding vector_cosine_ops);

create table part_symptoms (
  part_id    bigint references parts(id),
  symptom_id bigint references symptoms(id),
  primary key (part_id, symptom_id)
);

create table installation_steps (
  id          bigserial primary key,
  part_id     bigint references parts(id),
  step_no     int not null,
  text        text not null,
  difficulty  text,
  est_minutes int,
  video_url   text
);

create table repair_guides (
  id              bigserial primary key,
  appliance_type  text not null,
  brand           text,
  title           text,
  body            text not null,
  source_url      text,
  likely_part_ids bigint[] default '{}',
  embedding       vector(768)
);
create index on repair_guides using hnsw (embedding vector_cosine_ops);

create table related_parts (
  part_id         bigint references parts(id),
  related_part_id bigint references parts(id),
  primary key (part_id, related_part_id)
);

create table carts (
  id uuid primary key default gen_random_uuid(),
  session_id text not null
);
create table cart_items (
  cart_id uuid references carts(id),
  part_id bigint references parts(id),
  qty     int not null default 1,
  primary key (cart_id, part_id)
);
create table orders (
  id           text primary key,
  session_id   text,
  status       text not null,
  tracking_url text
);
create table chat_sessions (
  session_id text primary key,
  messages   jsonb not null default '[]',
  updated_at timestamptz default now()
);
```

```bash
# Run it
psql "$DATABASE_URL" -f db/schema.sql
```

## 5. Database connection

```python
# backend/db.py
import asyncpg
import os

pool: asyncpg.Pool | None = None

async def get_pool() -> asyncpg.Pool:
    global pool
    if pool is None:
        pool = await asyncpg.create_pool(os.environ["DATABASE_URL"])
    return pool

async def get_conn():
    p = await get_pool()
    return await p.acquire()

async def release_conn(conn):
    p = await get_pool()
    await p.release(conn)
```

## 6. Embedding helper

```python
# backend/embeddings.py
from google import genai
import os

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

async def embed(text: str) -> list[float]:
    response = client.models.embed_content(
        model="text-embedding-004",
        content=text,
    )
    return response.embeddings[0].values
```

## 7. Scraper & seed

*(Scraper code identical to the uploaded doc §3 — `httpx` + `BeautifulSoup4`, parses
product pages and model pages, extracts structured fields, loads into Supabase. See the
`backend/scraper/extract.py` and `backend/scraper/load.py` from the previous version.)*

```bash
# Seed the database with ~50-100 parts covering the example queries
python -m backend.scraper.seed
```

**Terminal checkpoint:** at this point, verify data exists:
```bash
psql "$DATABASE_URL" -c "select ps_number, name, price_cents from parts limit 5;"
psql "$DATABASE_URL" -c "select * from part_compatibility limit 5;"
psql "$DATABASE_URL" -c "select description from symptoms limit 5;"
```

---

# PHASE 2 — The Brain (LangChain)

## 8. LLM setup

```python
# backend/llm.py
from langchain_google_genai import ChatGoogleGenerativeAI
import os

flash = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    google_api_key=os.environ["GEMINI_API_KEY"],
    temperature=0.2,
)

flash_lite = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash-lite",
    google_api_key=os.environ["GEMINI_API_KEY"],
    temperature=0,
)

# Swap to another provider — one-line change:
# from langchain_openai import ChatOpenAI
# flash = ChatOpenAI(model="gpt-4o")
```

## 9. Tools (LangChain `@tool`)

The `@tool` decorator auto-generates the schema from type annotations + docstring.
Tools use a global connection pool — clean enough for a case study, and avoids
complex dependency injection.

```python
# backend/tools/catalog_tools.py
from langchain_core.tools import tool
from backend.db import get_pool
from backend.embeddings import embed

@tool
async def lookup_part(identifier: str) -> dict:
    """Get details for a part by PS number (e.g. PS11752778) or manufacturer
    part number. Returns price, stock, description, rating, appliance_type."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            select * from parts where ps_number = $1
            union all
            select p.* from parts p
            join part_cross_refs cr on cr.part_id = p.id
            where cr.alt_number = $1
            limit 1
        """, identifier)
    if not row:
        return {"error": "part_not_found", "identifier": identifier,
                "hint": "Double-check the number, or try the manufacturer part number."}
    return dict(row)

@tool
async def check_compatibility(ps_number: str, model_number: str) -> dict:
    """Deterministically check if a part fits a specific appliance model.
    Returns a boolean from the compatibility matrix plus both appliance types
    so the agent can explain cross-type mismatches."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        part = await conn.fetchrow(
            "select id, appliance_type from parts where ps_number=$1", ps_number)
        model = await conn.fetchrow(
            "select id, appliance_type from appliance_models where model_number=$1", model_number)
        if not part:
            return {"error": "part_not_found", "ps_number": ps_number}
        if not model:
            return {"error": "model_not_found", "model_number": model_number,
                    "hint": "Model numbers look like WDT780SAEM1. Check the tag inside your appliance door."}
        row = await conn.fetchrow("""
            select exists (
                select 1 from part_compatibility where part_id=$1 and model_id=$2
            ) as compatible
        """, part["id"], model["id"])
    return {
        "compatible": row["compatible"],
        "ps_number": ps_number, "model_number": model_number,
        "part_appliance_type": part["appliance_type"],
        "model_appliance_type": model["appliance_type"],
    }

@tool
async def get_installation_info(ps_number: str) -> dict:
    """Get ordered installation steps, difficulty, and video for a part."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            select s.* from installation_steps s
            join parts p on p.id = s.part_id
            where p.ps_number = $1 order by s.step_no
        """, ps_number)
    if not rows:
        return {"error": "no_install_info", "ps_number": ps_number,
                "hint": "Installation info isn't available for this part yet."}
    return {"ps_number": ps_number, "steps": [dict(r) for r in rows]}

@tool
async def find_parts_by_symptom(symptom: str, appliance_type: str) -> dict:
    """Find parts that fix a specific symptom. Uses BOTH exact symptom matching
    and fuzzy vector search. appliance_type must be 'refrigerator' or 'dishwasher'."""
    vec = await embed(symptom)
    pool = await get_pool()
    async with pool.acquire() as conn:
        exact = await conn.fetch("""
            select p.* from parts p
            join part_symptoms ps on ps.part_id = p.id
            join symptoms s on s.id = ps.symptom_id
            where s.appliance_type = $1 and s.description ilike $2
        """, appliance_type, f"%{symptom}%")
        fuzzy = await conn.fetch("""
            select s.description as matched_symptom, p.*,
                   1 - (s.embedding <=> $1::vector) as score
            from symptoms s
            join part_symptoms ps on ps.symptom_id = s.id
            join parts p on p.id = ps.part_id
            where s.appliance_type = $2
            order by s.embedding <=> $1::vector limit 5
        """, str(vec), appliance_type)
    return {"exact_matches": [dict(r) for r in exact],
            "fuzzy_matches": [dict(r) for r in fuzzy]}
```

```python
# backend/tools/repair_tools.py
from langchain_core.tools import tool
from backend.db import get_pool
from backend.embeddings import embed

@tool
async def search_repair_guides(query: str, appliance_type: str,
                                brand: str = None) -> list[dict]:
    """Semantic search over repair guides and Q&A for troubleshooting advice.
    appliance_type must be 'refrigerator' or 'dishwasher'."""
    vec = await embed(query)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            select id, title, body, source_url, likely_part_ids,
                   1 - (embedding <=> $1::vector) as score
            from repair_guides
            where appliance_type = $2
              and ($3::text is null or brand ilike $3)
            order by embedding <=> $1::vector limit 3
        """, str(vec), appliance_type, brand)
    return [dict(r) for r in rows]
```

```python
# backend/tools/order_tools.py
from langchain_core.tools import tool
from backend.db import get_pool

@tool
async def add_to_cart(ps_number: str, qty: int = 1) -> dict:
    """Add a part to the session cart. Returns updated cart summary."""
    # upsert logic here
    return {"added": ps_number, "qty": qty, "status": "ok"}

@tool
async def get_order_status(order_id: str) -> dict:
    """Look up a PartSelect order by order ID (e.g. ORD-10293)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("select * from orders where id=$1", order_id)
    if not row:
        return {"error": "order_not_found", "order_id": order_id}
    return dict(row)
```

## 10. Agents (LangChain AgentExecutor + agents-as-tools)

Each specialist is an `AgentExecutor` with its own prompt and tool subset. Then each
executor is wrapped as a `Tool` for the orchestrator — this is the agents-as-tools pattern.

```python
# backend/agents/specialists.py
from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import Tool
from backend.llm import flash
from backend.tools.catalog_tools import (
    lookup_part, check_compatibility, get_installation_info, find_parts_by_symptom,
)
from backend.tools.repair_tools import search_repair_guides
from backend.tools.order_tools import add_to_cart, get_order_status
from backend.prompts import CATALOG_AGENT_PROMPT, REPAIR_AGENT_PROMPT, ORDER_AGENT_PROMPT

def _make_executor(system_prompt: str, tools: list, max_iterations: int = 4) -> AgentExecutor:
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", "{input}"),
        MessagesPlaceholder("agent_scratchpad"),
    ])
    agent = create_tool_calling_agent(flash, tools, prompt)
    return AgentExecutor(
        agent=agent,
        tools=tools,
        max_iterations=max_iterations,
        return_intermediate_steps=True,   # we need tool results for UI cards
        handle_parsing_errors=True,
    )

# ── Specialist executors ──────────────────────────────────────────
catalog_executor = _make_executor(
    CATALOG_AGENT_PROMPT,
    [lookup_part, check_compatibility, get_installation_info, find_parts_by_symptom],
)

repair_executor = _make_executor(
    REPAIR_AGENT_PROMPT,
    [search_repair_guides, find_parts_by_symptom, lookup_part],
)

order_executor = _make_executor(
    ORDER_AGENT_PROMPT,
    [add_to_cart, get_order_status, lookup_part],
)

# ── Wrap each executor as a Tool for the orchestrator ─────────────
async def _run_catalog(query: str) -> str:
    result = await catalog_executor.ainvoke({"input": query})
    return _format_agent_result(result)

async def _run_repair(query: str) -> str:
    result = await repair_executor.ainvoke({"input": query})
    return _format_agent_result(result)

async def _run_order(query: str) -> str:
    result = await order_executor.ainvoke({"input": query})
    return _format_agent_result(result)

def _format_agent_result(result: dict) -> str:
    """Combine text + tool results into a structured JSON string the
    orchestrator and frontend can parse."""
    import json
    tool_results = []
    for action, observation in result.get("intermediate_steps", []):
        tool_results.append({
            "tool_name": action.tool,
            "args": action.tool_input,
            "result": observation,
        })
    return json.dumps({
        "text": result.get("output", ""),
        "tool_results": tool_results,
    })

catalog_agent_tool = Tool(
    name="catalog_agent",
    description=(
        "Handles part lookups, compatibility checks, installation info, and finding "
        "parts by symptom. Use for: 'find part X', 'is X compatible with Y', "
        "'how to install X', 'what part fixes [symptom]'."
    ),
    coroutine=_run_catalog,
    func=lambda q: None,   # async-only
)

repair_agent_tool = Tool(
    name="repair_agent",
    description=(
        "Handles troubleshooting and repair guidance. Use for: 'my fridge is leaking', "
        "'ice maker not working', 'how to fix X'."
    ),
    coroutine=_run_repair,
    func=lambda q: None,
)

order_agent_tool = Tool(
    name="order_agent",
    description=(
        "Handles cart operations and order tracking. Use for: 'add to cart', "
        "'where is my order', 'check order ORD-XXX'."
    ),
    coroutine=_run_order,
    func=lambda q: None,
)
```

## 11. Orchestrator

```python
# backend/agents/orchestrator.py
from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from backend.llm import flash
from backend.agents.specialists import catalog_agent_tool, repair_agent_tool, order_agent_tool
from backend.prompts import ORCHESTRATOR_PROMPT

agent_tools = [catalog_agent_tool, repair_agent_tool, order_agent_tool]

prompt = ChatPromptTemplate.from_messages([
    ("system", ORCHESTRATOR_PROMPT),
    ("human", "{input}"),
    MessagesPlaceholder("agent_scratchpad"),
])

orchestrator_agent = create_tool_calling_agent(flash, agent_tools, prompt)

orchestrator = AgentExecutor(
    agent=orchestrator_agent,
    tools=agent_tools,
    max_iterations=3,
    return_intermediate_steps=True,
    handle_parsing_errors=True,
)

async def run_orchestrator(user_input: str, context: str = "") -> dict:
    """Run the full agent pipeline. Returns text + tool_results."""
    import json
    full_input = f"Conversation context:\n{context}\n\nUser: {user_input}" if context else user_input
    result = await orchestrator.ainvoke({"input": full_input})

    # Parse nested agent results
    all_tool_results = []
    for action, observation in result.get("intermediate_steps", []):
        try:
            parsed = json.loads(observation)
            all_tool_results.extend(parsed.get("tool_results", []))
        except (json.JSONDecodeError, TypeError):
            pass

    return {
        "text": result.get("output", ""),
        "tool_results": all_tool_results,
        "agent": next((a.tool for a, _ in result.get("intermediate_steps", [])), None),
    }
```

## 12. Guardrail

```python
# backend/guardrail.py
from backend.llm import flash_lite

async def is_in_scope(message: str) -> bool:
    response = await flash_lite.ainvoke(
        f"""Classify this message. Reply ONLY 'IN' or 'OUT'.
IN = about refrigerator/dishwasher parts, compatibility, installation,
repair for those two types, or PartSelect orders.
OUT = anything else.

Message: {message}"""
    )
    text = response.content.strip()
    return "IN" in text and "OUT" not in text
```

## 13. Memory

```python
# backend/memory.py
import json

async def load_session(conn, session_id: str) -> list[dict]:
    row = await conn.fetchrow(
        "select messages from chat_sessions where session_id=$1", session_id)
    return json.loads(row["messages"]) if row else []

async def save_session(conn, session_id: str, messages: list[dict]):
    await conn.execute("""
        insert into chat_sessions (session_id, messages, updated_at)
        values ($1, $2, now())
        on conflict (session_id) do update
          set messages = excluded.messages, updated_at = now()
    """, session_id, json.dumps(messages))
```

## 14. System prompts

```python
# backend/prompts.py

ORCHESTRATOR_PROMPT = """You are the PartSelect assistant orchestrator.
Route each user message to the right specialist agent.

AGENTS:
- catalog_agent: part lookups, compatibility checks, installation info, finding parts by symptom
- repair_agent: troubleshooting, diagnosis, repair guides, "my X isn't working"
- order_agent: cart operations, order status tracking

RULES:
1. Pick ONE agent per turn. Pass the full user query as input.
2. Synthesize the agent's response into a helpful, conversational answer.
3. NEVER invent part numbers, prices, compatibility, or stock status.
4. When a compatibility check returns false with different appliance_types,
   explicitly explain the mismatch.
5. If a tool returns an error with a hint, relay the hint helpfully."""

CATALOG_AGENT_PROMPT = """You are the PartSelect catalog specialist.
You help users find parts, check compatibility, get installation instructions,
and identify which parts fix specific symptoms.

RULES:
- Extract PS numbers (PS#######) and model numbers exactly from the query.
- For compatibility checks, ALWAYS use check_compatibility. Never guess.
- When check_compatibility returns compatible=false with mismatched appliance_types,
  clearly state: "This is a [type] part, but your model is a [type]."
- For "what part fixes X" questions, use find_parts_by_symptom.
- Always include price, stock status, and rating in your response.
- NEVER invent facts. Only state what tools returned."""

REPAIR_AGENT_PROMPT = """You are the PartSelect repair specialist.
You diagnose appliance problems and recommend replacement parts.

RULES:
- Ask for appliance type (refrigerator/dishwasher) and brand if not obvious.
- Use search_repair_guides for troubleshooting advice.
- Use find_parts_by_symptom to identify likely replacement parts.
- Use lookup_part for full details (price, stock) on recommended parts.
- Present diagnosis first, then recommend specific parts with prices.
- Link to repair videos when available."""

ORDER_AGENT_PROMPT = """You are the PartSelect order specialist.
You help users manage their cart and track orders.

RULES:
- For add_to_cart, confirm part name and price before adding.
- For order status, relay current status and tracking info.
- If an order or part isn't found, provide the helpful hint from the error."""
```

---

# PHASE 3 — Terminal Testing

## 15. Test individual tools

```python
# test_cli.py  — run with: python -m test_cli
import asyncio
from dotenv import load_dotenv
load_dotenv()

from backend.db import get_pool
from backend.tools.catalog_tools import lookup_part, check_compatibility

async def test_tools():
    pool = await get_pool()

    print("=== lookup_part ===")
    result = await lookup_part.ainvoke({"identifier": "PS11752778"})
    print(result)

    print("\n=== check_compatibility (THE TRAP — should be False) ===")
    result = await check_compatibility.ainvoke({
        "ps_number": "PS11752778", "model_number": "WDT780SAEM1"
    })
    print(result)
    assert result["compatible"] is False, "TRAP QUERY FAILED!"
    assert result["part_appliance_type"] == "refrigerator"
    assert result["model_appliance_type"] == "dishwasher"
    print("✓ Trap query passed — fridge part vs dishwasher model = incompatible")

    print("\n=== check_compatibility (should be True) ===")
    result = await check_compatibility.ainvoke({
        "ps_number": "PS11752778", "model_number": "WRS322FDAM00"
    })
    print(result)

    await pool.close()

asyncio.run(test_tools())
```

```bash
python -m test_cli
```

## 16. Test individual agents

```python
# test_agents.py
import asyncio
from dotenv import load_dotenv
load_dotenv()

from backend.db import get_pool
from backend.agents.specialists import catalog_executor, repair_executor

async def test_agents():
    pool = await get_pool()

    print("=== Catalog Agent: installation query ===")
    result = await catalog_executor.ainvoke({
        "input": "How can I install part number PS11752778?"
    })
    print(f"Output: {result['output']}")
    print(f"Steps: {len(result.get('intermediate_steps', []))} tool calls")
    for action, obs in result.get("intermediate_steps", []):
        print(f"  → {action.tool}({action.tool_input})")

    print("\n=== Catalog Agent: THE TRAP QUERY ===")
    result = await catalog_executor.ainvoke({
        "input": "Is PS11752778 compatible with WDT780SAEM1?"
    })
    print(f"Output: {result['output']}")
    # Verify the agent mentions the appliance type mismatch
    output = result["output"].lower()
    assert "refrigerator" in output or "dishwasher" in output, \
        "Agent should mention the appliance type mismatch!"
    print("✓ Agent correctly identified cross-appliance incompatibility")

    print("\n=== Repair Agent: symptom query ===")
    result = await repair_executor.ainvoke({
        "input": "The ice maker on my Whirlpool fridge is not working. How can I fix it?"
    })
    print(f"Output: {result['output']}")

    await pool.close()

asyncio.run(test_agents())
```

```bash
python -m test_agents
```

## 17. Test the full orchestrator

```python
# test_orchestrator.py
import asyncio
from dotenv import load_dotenv
load_dotenv()

from backend.db import get_pool
from backend.agents.orchestrator import run_orchestrator
from backend.guardrail import is_in_scope

async def test_full_flow():
    pool = await get_pool()

    # Test guardrail
    print("=== Guardrail ===")
    assert await is_in_scope("How can I install PS11752778?") == True
    assert await is_in_scope("Write me a poem about cats") == False
    assert await is_in_scope("Help me fix my washing machine") == False  # out of scope: washers
    print("✓ Guardrail passed")

    # Test full orchestrator with all 3 example queries
    queries = [
        "How can I install part number PS11752778?",
        "Is PS11752778 compatible with my WDT780SAEM1 model?",
        "The ice maker on my Whirlpool fridge is not working. How can I fix it?",
    ]

    for q in queries:
        print(f"\n=== Query: {q} ===")
        if not await is_in_scope(q):
            print("  OUT OF SCOPE (unexpected!)")
            continue
        result = await run_orchestrator(q)
        print(f"  Agent: {result['agent']}")
        print(f"  Tool calls: {len(result['tool_results'])}")
        for tr in result["tool_results"]:
            print(f"    → {tr['tool_name']}({tr['args']})")
        print(f"  Response: {result['text'][:200]}...")

    await pool.close()

asyncio.run(test_full_flow())
```

```bash
python -m test_orchestrator
# Expected output:
#   Query 1 → catalog_agent → lookup_part + get_installation_info
#   Query 2 → catalog_agent → check_compatibility → compatible: false (TRAP)
#   Query 3 → repair_agent → search_repair_guides + find_parts_by_symptom + lookup_part
```

**Terminal checkpoint:** all three example queries produce correct tool calls and accurate
responses before any API or UI code is written. If anything fails here, fix it before
proceeding.

---

# PHASE 4 — API

## 18. FastAPI endpoint with SSE

```python
# backend/main.py
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import json, os
from dotenv import load_dotenv
load_dotenv()

from backend.db import get_pool, release_conn
from backend.guardrail import is_in_scope
from backend.agents.orchestrator import run_orchestrator
from backend.memory import save_session

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000"],
                   allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
async def startup():
    await get_pool()

@app.post("/chat")
async def chat(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    session_id = body.get("session_id", "default")

    async def event_stream():
        last_user_msg = next(
            (m["content"] for m in reversed(messages) if m["role"] == "user"), "")

        # 1) Guardrail
        if not await is_in_scope(last_user_msg):
            yield _sse("text", "I'm the PartSelect parts assistant — I can help "
                "with refrigerator and dishwasher parts, compatibility, installation, "
                "repairs, and order tracking. What can I help you find?")
            yield _sse("done", {})
            return

        yield _sse("tool_status", {"state": "routing", "message": "Finding the right specialist…"})

        # 2) Build context from recent messages
        context = "\n".join(f"{m['role']}: {m['content']}" for m in messages[-6:])

        # 3) Run orchestrator
        result = await run_orchestrator(last_user_msg, context)

        # 4) Stream tool results (frontend renders as cards)
        for tr in result.get("tool_results", []):
            yield _sse("tool_result", tr)

        # 5) Stream text
        yield _sse("text", result.get("text", ""))
        yield _sse("done", {"agent": result.get("agent")})

        # 6) Persist session
        pool = await get_pool()
        async with pool.acquire() as conn:
            assistant_msg = {"role": "assistant", "content": result.get("text", "")}
            await save_session(conn, session_id, messages + [assistant_msg])

    return StreamingResponse(event_stream(), media_type="text/event-stream")

def _sse(event: str, data) -> str:
    payload = json.dumps(data) if isinstance(data, (dict, list)) else json.dumps(data)
    return f"event: {event}\ndata: {payload}\n\n"
```

**Test the API from terminal:**

```bash
# Terminal 1:
uvicorn backend.main:app --reload --port 8000

# Terminal 2:
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "Is PS11752778 compatible with WDT780SAEM1?"}]}'
```

---

# PHASE 5 — Frontend (built last)

## 19. Frontend setup

```bash
cd frontend
npx create-next-app@latest . --ts --app   # decline Tailwind
# No AI SDK needed — we use a custom hook
```

## 20. Custom `useAgentChat` hook

```tsx
// frontend/hooks/useAgentChat.ts
"use client";
import { useState, useCallback, useRef } from "react";

export interface ToolResult {
  tool_name: string;
  args: Record<string, any>;
  result: Record<string, any>;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  toolResults?: ToolResult[];
}

export function useAgentChat({ sessionId }: { sessionId: string }) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [status, setStatus] = useState<"idle" | "streaming">("idle");
  const [toolStatus, setToolStatus] = useState<string | null>(null);
  const idRef = useRef(0);

  const sendMessage = useCallback(async (text: string) => {
    const userMsg: ChatMessage = {
      id: `msg-${++idRef.current}`, role: "user", content: text,
    };
    const updated = [...messages, userMsg];
    setMessages(updated);
    setStatus("streaming");
    setToolStatus(null);

    const assistantId = `msg-${++idRef.current}`;
    let assistantText = "";
    const toolResults: ToolResult[] = [];

    try {
      const resp = await fetch("http://localhost:8000/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          messages: updated.map(m => ({ role: m.role, content: m.content })),
          session_id: sessionId,
        }),
      });

      const reader = resp.body!.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        const blocks = buffer.split("\n\n");
        buffer = blocks.pop() || "";

        for (const block of blocks) {
          const eventMatch = block.match(/^event: (\w+)/);
          const dataMatch = block.match(/data: (.*)/s);
          if (!eventMatch || !dataMatch) continue;
          const [, eventType] = eventMatch;
          const data = JSON.parse(dataMatch[1]);

          if (eventType === "tool_status") setToolStatus(data.message);
          else if (eventType === "tool_result") toolResults.push(data);
          else if (eventType === "text") assistantText = data;
        }
      }

      setMessages(prev => [...prev, {
        id: assistantId, role: "assistant", content: assistantText, toolResults,
      }]);
    } catch {
      setMessages(prev => [...prev, {
        id: assistantId, role: "assistant",
        content: "Sorry, something went wrong. Please try again.", toolResults: [],
      }]);
    } finally {
      setStatus("idle");
      setToolStatus(null);
    }
  }, [messages, sessionId]);

  return { messages, sendMessage, status, toolStatus };
}
```

## 21. Chat component + UI

```tsx
// frontend/app/components/Chat.tsx
"use client";
import { useAgentChat, ToolResult } from "../../hooks/useAgentChat";
import styles from "./Chat.module.css";
import { ProductCard } from "./ProductCard";
import { CompatVerdict } from "./CompatVerdict";
import { InstallDrawer } from "./InstallDrawer";
import { SuggestedPrompts } from "./SuggestedPrompts";

const SUGGESTIONS = [
  "Find a part for my refrigerator",
  "Check if a part fits my model",
  "My dishwasher isn't draining",
  "Track my order",
];

export function Chat({ sessionId }: { sessionId: string }) {
  const { messages, sendMessage, status, toolStatus } = useAgentChat({ sessionId });

  return (
    <div className={styles.container}>
      <header className={styles.header}>
        <img src="/ps-logo.svg" alt="PartSelect" className={styles.logo} />
        <span className={styles.badge}>Parts Assistant</span>
      </header>

      <div className={styles.messages}>
        {messages.length === 0 && (
          <div className={styles.welcome}>
            <h2>Hi! I'm the PartSelect parts assistant.</h2>
            <p>I can help you find refrigerator and dishwasher parts, check
               compatibility, troubleshoot problems, and track orders.</p>
            <SuggestedPrompts prompts={SUGGESTIONS} onSelect={sendMessage} />
          </div>
        )}

        {messages.map((m) => (
          <div key={m.id} className={m.role === "user" ? styles.userRow : styles.assistantRow}>
            {m.toolResults?.map((tr, i) => <ToolResultCard key={i} result={tr} />)}
            {m.content && <div className={styles.bubble}>{m.content}</div>}
          </div>
        ))}

        {toolStatus && <div className={styles.thinking}>{toolStatus}</div>}
        {status === "streaming" && !toolStatus && (
          <div className={styles.typing}><span/><span/><span/></div>
        )}
      </div>

      <div className={styles.inputArea}>
        <Composer onSend={sendMessage} disabled={status === "streaming"} />
      </div>
    </div>
  );
}

function ToolResultCard({ result }: { result: ToolResult }) {
  const { tool_name, result: data } = result;
  if (tool_name === "lookup_part" && !data.error) return <ProductCard data={data} />;
  if (tool_name === "check_compatibility")          return <CompatVerdict data={data} />;
  if (tool_name === "get_installation_info" && !data.error) return <InstallDrawer data={data} />;
  return null;
}
```

*(Brand tokens in `globals.css`, ProductCard.module.css, CompatVerdict, InstallDrawer,
SuggestedPrompts, Composer — all identical to the previous version. Pure CSS Modules,
no Tailwind.)*

---

## 22. Error handling

*(Same error table as previous version. Error objects from Python tools include `hint`
fields; the agent relays them. See §13 of the uploaded doc.)*

---

## 23. End-to-end walkthroughs

*(Same three queries, same expected flows. See §14 of the uploaded doc. The only
difference is the execution layer: LangChain AgentExecutor instead of manual loop.)*

---

## 24. Evaluation (pytest)

```python
# eval/test_suite.py
import pytest
from backend.tools.catalog_tools import lookup_part, check_compatibility
from backend.guardrail import is_in_scope

@pytest.mark.asyncio
async def test_trap_query():
    """PS11752778 (fridge) vs WDT780SAEM1 (dishwasher) → incompatible."""
    result = await check_compatibility.ainvoke({
        "ps_number": "PS11752778", "model_number": "WDT780SAEM1"})
    assert result["compatible"] is False
    assert result["part_appliance_type"] == "refrigerator"
    assert result["model_appliance_type"] == "dishwasher"

@pytest.mark.asyncio
async def test_compatible():
    result = await check_compatibility.ainvoke({
        "ps_number": "PS11752778", "model_number": "WRS322FDAM00"})
    assert result["compatible"] is True

@pytest.mark.asyncio
async def test_part_not_found():
    result = await lookup_part.ainvoke({"identifier": "PS99999999"})
    assert result["error"] == "part_not_found"

@pytest.mark.asyncio
async def test_guardrail_in():
    assert await is_in_scope("Is PS11752778 compatible with my fridge?")

@pytest.mark.asyncio
async def test_guardrail_out():
    assert not await is_in_scope("Write me a poem about cats")

@pytest.mark.asyncio
async def test_guardrail_washer_out_of_scope():
    assert not await is_in_scope("Help me fix my washing machine")
```

```bash
pytest eval/ -v
```

---

## 25. Extensibility & scalability

- **Swap LLM:** change one line in `llm.py` — `ChatGoogleGenerativeAI` → `ChatOpenAI` → `ChatAnthropic`. Tools unchanged.
- **New appliance:** add `appliance_type` value + seed data + optionally a new agent.
- **New capability:** write a `@tool` function + add it to an agent's tool list.
- **New agent:** write an executor, wrap as `Tool`, append to `agent_tools` in the orchestrator.
- **Scale:** FastAPI runs behind gunicorn/uvicorn workers; asyncpg pool handles concurrent DB load.

## 26. LangGraph upgrade path (documented, not used)

If flows need durable checkpointed state, deterministic compliance edges, or
human-in-the-loop approval gates, replace `AgentExecutor` with
`langgraph.prebuilt.create_react_agent` or a custom `StateGraph`. The `@tool`
functions, prompts, and database layer carry over unchanged — only the orchestration
wrapper swaps.

---

## 27. Repo structure

```
partselect-agent/
├── backend/
│   ├── main.py                   # FastAPI app (§18)
│   ├── llm.py                    # LangChain LLM setup (§8)
│   ├── db.py                     # asyncpg pool (§5)
│   ├── embeddings.py             # google-genai embed() (§6)
│   ├── guardrail.py              # scope classifier (§12)
│   ├── memory.py                 # session persistence (§13)
│   ├── prompts.py                # all system prompts (§14)
│   ├── agents/
│   │   ├── specialists.py        # 3 executors + agent-tools (§10)
│   │   └── orchestrator.py       # top-level routing (§11)
│   ├── tools/
│   │   ├── catalog_tools.py      # lookup, compat, install, symptom (§9)
│   │   ├── repair_tools.py       # search_repair_guides
│   │   └── order_tools.py        # cart, order status
│   └── scraper/
│       ├── extract.py            # parse HTML (§7)
│       └── load.py               # upsert to Supabase
├── frontend/
│   ├── app/
│   │   ├── globals.css
│   │   ├── page.tsx
│   │   └── components/ ...       # Chat, ProductCard, etc. (§21)
│   └── hooks/
│       └── useAgentChat.ts       # custom SSE hook (§20)
├── db/
│   └── schema.sql                # §4
├── eval/
│   └── test_suite.py             # pytest (§24)
├── test_cli.py                   # tool tests (§15)
├── test_agents.py                # agent tests (§16)
├── test_orchestrator.py          # full flow tests (§17)
├── requirements.txt
└── .env
```

## 28. Build sequence (the actual order you do things)

```
Phase 1 — Foundation
  1. Create .env, install deps, set up venv
  2. Run schema.sql against Supabase
  3. Run scraper/seed to populate data
  4. Verify with psql: parts, compatibility, symptoms exist

Phase 2 — Brain
  5. Write llm.py, embeddings.py, db.py
  6. Write tools (catalog, repair, order)
  7. Write prompts.py
  8. Write specialists.py (3 agent executors)
  9. Write orchestrator.py
  10. Write guardrail.py, memory.py

Phase 3 — Terminal testing
  11. python -m test_cli          ← tools work?
  12. python -m test_agents       ← agents call the right tools?
  13. python -m test_orchestrator ← full flow routes correctly?
  14. pytest eval/ -v             ← all assertions pass?

Phase 4 — API
  15. Write main.py (FastAPI + SSE)
  16. uvicorn backend.main:app --reload
  17. curl test the /chat endpoint

Phase 5 — Frontend
  18. npx create-next-app
  19. Write useAgentChat hook
  20. Write Chat.tsx + components
  21. Write CSS Modules
  22. npm run dev, test in browser
```

Every phase has a terminal checkpoint before the next begins. If Phase 3 fails,
you don't waste time building an API that serves wrong answers.
