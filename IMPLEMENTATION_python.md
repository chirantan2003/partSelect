# PartSelect Chat Agent — Complete Implementation Guide

> A transactional chat agent for PartSelect's Refrigerator & Dishwasher parts catalog.
> **Python backend (FastAPI + Gemini) handles all logic; TypeScript frontend (Next.js)
> handles all rendering.** The LLM handles language, the database handles truth, and
> the seam between them is a small set of typed tools routed through specialist agents.

---

## 0. Stack at a glance

| Layer | Choice | Why |
|---|---|---|
| Frontend | Next.js (App Router) + TypeScript + CSS Modules | Streaming chat UI, brand-matched styling, scoped CSS |
| Bridge | Custom `useAgentChat` hook → FastAPI SSE | Clean typed event protocol, no proxy layer needed |
| Backend | **FastAPI** (Python) | All agent logic, tools, guardrail, streaming |
| LLM | **google-genai** Python SDK — Gemini 2.5 Flash + Flash-Lite | Free tier, native function calling, streaming |
| Truth engine | Supabase (PostgreSQL + `pgvector`) via **asyncpg** | Relational truth + semantic search in one engine |
| Scraper | **httpx** + **BeautifulSoup4** (Python) | Data acquisition pipeline |
| Validation | **Pydantic** (replaces Zod) | Tool input/output schemas |

**Why Python backend:** the scraper, embeddings pipeline, database tools, agent loop, and
eval harness are all naturally Python. Only the UI rendering needs TypeScript. This split
puts ~80% of the code in Python and avoids maintaining parallel logic in two languages.

---

## 1. Design principles

*(Unchanged — these are language-agnostic.)*

### 1.1 Language → Truth → UI
The model never invents facts. Every fact comes from the database via typed tools.
Extending = "add data + add a tool + optionally add an agent."

### 1.2 Deterministic vs. semantic — never confuse them
**The trap in query #2:** PS11752778 is a *Refrigerator Door Shelf Bin*. WDT780SAEM1 is a
*Whirlpool Dishwasher*. Compatibility is a SQL join returning a boolean, never an embedding.

### 1.3 Stay in scope, cheaply
Flash-Lite classify-first gate. Off-topic traffic never reaches the agents or tools.

---

## 2. Architecture overview

```
┌──────────────────────────────────────────────────────────────────┐
│  BROWSER  (Next.js + TypeScript + CSS Modules)                    │
│                                                                   │
│  useAgentChat() → SSE stream → message state + tool-result cards  │
│  Welcome · SuggestedPrompts · ProductCard · CompatVerdict ·       │
│  InstallDrawer · OrderStatus · Composer                           │
└──────────▲──────────────────────────────────────┬────────────────┘
           │  Server-Sent Events                   │  POST /chat
           │  event: text | tool_status |          │  { messages, session_id }
           │  tool_result | error | done           │
           │                                       ▼
┌──────────┴───────────────────────────────────────────────────────┐
│  FASTAPI  (Python)  http://localhost:8000                         │
│                                                                   │
│  1) guardrail(msg) ── OUT ──► SSE: refusal text                  │
│           │ IN_SCOPE                                              │
│           ▼                                                       │
│  2) orchestrator classifies intent → pick agent                   │
│        ┌───────────┼───────────┐                                 │
│        ▼           ▼           ▼                                 │
│   CatalogAgent  RepairAgent  OrderAgent                          │
│   ┌──────────┐ ┌──────────┐ ┌──────────┐                        │
│   │lookup    │ │search    │ │add_to    │                         │
│   │compat   │ │repair   │ │cart      │                          │
│   │install  │ │symptom  │ │order    │                           │
│   │symptom  │ │lookup   │ │status   │                           │
│   └──────────┘ └──────────┘ └──────────┘                        │
│        │           │           │                                 │
│        └───────────┴───────────┘                                 │
│                    │  await tool_fn()                             │
│                    ▼                                              │
│  3) stream response: text tokens + tool_result events            │
│  4) save session → Supabase                                      │
└────────────────────┬─────────────────────────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────────────────────────┐
│  SUPABASE  (PostgreSQL + pgvector)                                │
│  parts · models · part_compatibility · symptoms · part_symptoms · │
│  repair_guides · installation_steps · carts · orders · sessions   │
└──────────────────────────────────────────────────────────────────┘
```

No proxy layer — the Next.js frontend calls FastAPI directly (CORS configured).
No Vercel AI SDK on the server — Python owns the agent loop and streaming.

---

## 3. Data acquisition pipeline (Python)

```python
# backend/scraper/extract.py
import httpx
from bs4 import BeautifulSoup
from dataclasses import dataclass, field

@dataclass
class ScrapedPart:
    ps_number: str
    mpn: str
    name: str
    description: str
    price_cents: int
    in_stock: bool
    image_url: str | None
    rating: float | None
    review_count: int
    appliance_type: str          # 'refrigerator' | 'dishwasher'
    video_url: str | None
    symptoms: list[str] = field(default_factory=list)
    alt_numbers: list[str] = field(default_factory=list)
    install_text: str | None = None

@dataclass
class ScrapedModel:
    model_number: str
    brand: str
    appliance_type: str
    part_ps_numbers: list[str] = field(default_factory=list)

def parse_product_page(html: str, appliance_type: str) -> ScrapedPart:
    soup = BeautifulSoup(html, "html.parser")

    # Try JSON-LD first (cleanest source if present)
    import json
    json_ld = None
    for script in soup.find_all("script", type="application/ld+json"):
        data = json.loads(script.string)
        if data.get("@type") == "Product":
            json_ld = data
            break

    ps_number = soup.select_one(".ps-partnum")
    ps_number = ps_number.text.replace("PartSelect #:", "").strip() if ps_number else ""

    mpn = soup.select_one(".mfr-partnum")
    mpn = mpn.text.replace("Manufacturer #:", "").strip() if mpn else ""

    name = soup.select_one("h1.title")
    name = name.text.strip() if name else ""

    description = soup.select_one(".description-text")
    description = description.text.strip() if description else ""

    # Price
    price_text = (json_ld or {}).get("offers", {}).get("price") \
        or (soup.select_one(".price") and soup.select_one(".price").text.replace("$", "").strip()) \
        or "0"
    price_cents = round(float(price_text) * 100)

    in_stock = "InStock" in str((json_ld or {}).get("offers", {}).get("availability", "")) \
        or bool(soup.select_one(".stock-status") and "In Stock" in soup.select_one(".stock-status").text)

    # "Fixes these symptoms" — the structured symptom mapping
    symptoms = [li.text.strip() for li in soup.select(".symptoms-list li, .fixes-symptoms li")]

    # Alternate part numbers
    alt_numbers = [mpn] if mpn else []
    for el in soup.select(".cross-ref-list span, .replaces-list span"):
        alt_numbers.append(el.text.strip())

    return ScrapedPart(
        ps_number=ps_number, mpn=mpn, name=name, description=description,
        price_cents=price_cents, in_stock=in_stock,
        image_url=(json_ld or {}).get("image"),
        rating=float(r) if (r := (json_ld or {}).get("aggregateRating", {}).get("ratingValue")) else None,
        review_count=int(rc) if (rc := (json_ld or {}).get("aggregateRating", {}).get("reviewCount")) else 0,
        appliance_type=appliance_type, video_url=None,
        symptoms=symptoms, alt_numbers=alt_numbers,
    )

def parse_model_page(html: str) -> ScrapedModel:
    soup = BeautifulSoup(html, "html.parser")
    model_number = soup.select_one(".model-number")
    model_number = model_number.text.strip() if model_number else ""

    title = (soup.select_one("h1") or soup.select_one("title")).text.lower()
    appliance_type = "dishwasher" if "dishwasher" in title else "refrigerator"

    ps_numbers = []
    import re
    for el in soup.select("[data-ps-number], .part-link"):
        ps = el.get("data-ps-number") or ""
        if not ps:
            match = re.search(r"PS\d+", el.text)
            ps = match.group() if match else ""
        if ps:
            ps_numbers.append(ps)

    return ScrapedModel(
        model_number=model_number, brand="", appliance_type=appliance_type,
        part_ps_numbers=ps_numbers,
    )

async def crawl_category(category_url: str, appliance_type: str) -> list[ScrapedPart]:
    """Crawl a category page, follow product links, parse each."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(category_url)
        soup = BeautifulSoup(resp.text, "html.parser")
        product_urls = [a["href"] for a in soup.select("a[href*='/PS']") if "/PS" in a.get("href", "")]

        parts = []
        for url in product_urls[:100]:   # cap for demo
            full_url = f"https://www.partselect.com{url}" if url.startswith("/") else url
            resp = await client.get(full_url)
            parts.append(parse_product_page(resp.text, appliance_type))
        return parts
```

### 3.1 Loader: scraped data → Supabase

```python
# backend/scraper/load.py
import asyncpg
from .extract import ScrapedPart, ScrapedModel
from backend.gemini_client import embed

async def load_part(conn: asyncpg.Connection, part: ScrapedPart):
    # 1) Upsert part
    row = await conn.fetchrow("""
        insert into parts (ps_number, mpn, name, description, price_cents,
                           in_stock, image_url, rating, review_count, appliance_type, video_url)
        values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
        on conflict (ps_number) do update set price_cents=excluded.price_cents, in_stock=excluded.in_stock
        returning id
    """, part.ps_number, part.mpn, part.name, part.description, part.price_cents,
        part.in_stock, part.image_url, part.rating, part.review_count,
        part.appliance_type, part.video_url)
    part_id = row["id"]

    # 2) Cross-reference numbers
    for alt in part.alt_numbers:
        await conn.execute("""
            insert into part_cross_refs (part_id, alt_number) values ($1, $2)
            on conflict do nothing
        """, part_id, alt)

    # 3) Symptoms — relational + embedded
    for desc in part.symptoms:
        vec = await embed(desc)
        symptom_row = await conn.fetchrow("""
            insert into symptoms (description, appliance_type, embedding)
            values ($1, $2, $3)
            on conflict (description) do nothing returning id
        """, desc, part.appliance_type, str(vec))
        symptom_id = symptom_row["id"] if symptom_row else \
            (await conn.fetchrow("select id from symptoms where description=$1", desc))["id"]
        await conn.execute("""
            insert into part_symptoms (part_id, symptom_id) values ($1, $2)
            on conflict do nothing
        """, part_id, symptom_id)

async def load_model(conn: asyncpg.Connection, model: ScrapedModel):
    row = await conn.fetchrow("""
        insert into appliance_models (model_number, brand, appliance_type)
        values ($1, $2, $3) on conflict (model_number) do nothing returning id
    """, model.model_number, model.brand, model.appliance_type)
    model_id = row["id"] if row else \
        (await conn.fetchrow("select id from appliance_models where model_number=$1", model.model_number))["id"]

    for ps in model.part_ps_numbers:
        await conn.execute("""
            insert into part_compatibility (part_id, model_id)
            select p.id, $2 from parts p where p.ps_number = $1
            on conflict do nothing
        """, ps, model_id)
```

### 3.2 Embedding strategy
- **Model:** `text-embedding-004` via google-genai (768 dimensions, free tier)
- **Embedded:** repair guide text (500-token chunks, 50-token overlap), Q&A pairs, symptom descriptions
- **Not embedded:** compatibility, price, stock, part numbers — these are exact SQL lookups

---

## 4. Data model

*(SQL schema identical to previous version — see §4 of the uploaded doc. Unchanged by language.)*

---

## 5. Tools (Python + Pydantic)

```python
# backend/tools/catalog.py
from pydantic import BaseModel, Field
import asyncpg

# ── Schemas ──────────────────────────────────────────────────
class LookupPartInput(BaseModel):
    identifier: str = Field(description="PS number (e.g. PS11752778) or manufacturer part number")

class CheckCompatibilityInput(BaseModel):
    ps_number: str
    model_number: str

class FindBySymptomInput(BaseModel):
    symptom: str
    appliance_type: str = Field(pattern="^(refrigerator|dishwasher)$")
    model_number: str | None = None

class InstallInfoInput(BaseModel):
    ps_number: str

# ── Tool functions ───────────────────────────────────────────
async def lookup_part(conn: asyncpg.Connection, inp: LookupPartInput) -> dict:
    row = await conn.fetchrow("""
        select * from parts where ps_number = $1
        union all
        select p.* from parts p
        join part_cross_refs cr on cr.part_id = p.id where cr.alt_number = $1
        limit 1
    """, inp.identifier)
    if not row:
        return {"error": "part_not_found", "identifier": inp.identifier,
                "hint": "Double-check the number, or try the manufacturer part number."}
    return dict(row)

async def check_compatibility(conn: asyncpg.Connection, inp: CheckCompatibilityInput) -> dict:
    part = await conn.fetchrow(
        "select id, appliance_type from parts where ps_number = $1", inp.ps_number)
    model = await conn.fetchrow(
        "select id, appliance_type from appliance_models where model_number = $1", inp.model_number)
    if not part:
        return {"error": "part_not_found", "ps_number": inp.ps_number}
    if not model:
        return {"error": "model_not_found", "model_number": inp.model_number,
                "hint": "Model numbers look like WDT780SAEM1. Check the tag inside your appliance door."}
    row = await conn.fetchrow("""
        select exists (
            select 1 from part_compatibility where part_id=$1 and model_id=$2
        ) as compatible
    """, part["id"], model["id"])
    return {
        "compatible": row["compatible"],
        "ps_number": inp.ps_number,
        "model_number": inp.model_number,
        "part_appliance_type": part["appliance_type"],
        "model_appliance_type": model["appliance_type"],
    }

async def find_parts_by_symptom(conn: asyncpg.Connection, inp: FindBySymptomInput,
                                 embed_fn) -> dict:
    vec = await embed_fn(inp.symptom)
    # Path 1: exact match
    exact = await conn.fetch("""
        select p.* from parts p
        join part_symptoms ps on ps.part_id = p.id
        join symptoms s on s.id = ps.symptom_id
        where s.appliance_type = $1 and s.description ilike $2
    """, inp.appliance_type, f"%{inp.symptom}%")
    # Path 2: fuzzy vector match
    fuzzy = await conn.fetch("""
        select s.description as matched_symptom, p.*,
               1 - (s.embedding <=> $1::vector) as score
        from symptoms s
        join part_symptoms ps on ps.symptom_id = s.id
        join parts p on p.id = ps.part_id
        where s.appliance_type = $2
        order by s.embedding <=> $1::vector limit 5
    """, str(vec), inp.appliance_type)
    return {"exact_matches": [dict(r) for r in exact],
            "fuzzy_matches": [dict(r) for r in fuzzy]}

async def get_installation_info(conn: asyncpg.Connection, inp: InstallInfoInput) -> dict:
    rows = await conn.fetch("""
        select s.* from installation_steps s
        join parts p on p.id = s.part_id
        where p.ps_number = $1 order by s.step_no
    """, inp.ps_number)
    if not rows:
        return {"error": "no_install_info", "ps_number": inp.ps_number,
                "hint": "Installation info isn't available for this part yet."}
    return {"ps_number": inp.ps_number, "steps": [dict(r) for r in rows]}
```

```python
# backend/tools/repair.py
async def search_repair_guides(conn, inp, embed_fn) -> list[dict]:
    vec = await embed_fn(inp.query)
    rows = await conn.fetch("""
        select id, title, body, source_url, likely_part_ids,
               1 - (embedding <=> $1::vector) as score
        from repair_guides
        where appliance_type = $2
          and ($3::text is null or brand ilike $3)
        order by embedding <=> $1::vector limit 3
    """, str(vec), inp.appliance_type, inp.brand)
    return [dict(r) for r in rows]
```

```python
# backend/tools/order.py
async def add_to_cart(conn, inp) -> dict:
    # upsert cart_items, return summary
    ...

async def get_order_status(conn, inp) -> dict:
    row = await conn.fetchrow("select * from orders where id = $1", inp.order_id)
    if not row:
        return {"error": "order_not_found", "order_id": inp.order_id}
    return dict(row)
```

---

## 6. Gemini integration (Python SDK)

```python
# backend/gemini_client.py
from google import genai
from google.genai import types
import os

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

FLASH = "gemini-2.5-flash"
FLASH_LITE = "gemini-2.5-flash-lite"

async def embed(text: str) -> list[float]:
    response = client.models.embed_content(
        model="text-embedding-004",
        content=text,
    )
    return response.embeddings[0].values

def build_function_declarations(tool_map: dict) -> list[types.Tool]:
    """Convert our Pydantic-schema tools into Gemini function declarations."""
    declarations = []
    for name, (description, schema_cls) in tool_map.items():
        props = {}
        required = []
        for field_name, field_info in schema_cls.model_fields.items():
            props[field_name] = {"type": "string", "description": field_info.description or ""}
            if field_info.is_required():
                required.append(field_name)
        declarations.append(types.FunctionDeclaration(
            name=name,
            description=description,
            parameters={"type": "object", "properties": props, "required": required},
        ))
    return [types.Tool(function_declarations=declarations)]

async def generate_with_tools(system_prompt: str, messages: list[dict],
                               tool_declarations, max_steps: int = 4,
                               execute_tool=None):
    """
    Manual tool-calling loop — equivalent to AI SDK's streamText + stopWhen.
    Calls Gemini, executes any function_calls, feeds results back, repeats.
    Returns final text + list of tool results.
    """
    contents = _convert_messages(messages)
    tool_results = []
    final_text = ""

    for _ in range(max_steps):
        response = client.models.generate_content(
            model=FLASH,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                tools=tool_declarations,
            ),
        )

        # Check for function calls in the response
        has_function_call = False
        for part in response.candidates[0].content.parts:
            if hasattr(part, "function_call") and part.function_call:
                has_function_call = True
                fc = part.function_call
                # Execute the tool
                result = await execute_tool(fc.name, fc.args)
                tool_results.append({"tool_name": fc.name, "args": dict(fc.args), "result": result})

                # Append function call + result to conversation
                contents.append(response.candidates[0].content)
                contents.append(types.Content(
                    role="user",
                    parts=[types.Part(function_response=types.FunctionResponse(
                        name=fc.name,
                        response={"result": result},
                    ))],
                ))
                break  # re-enter the loop so model can reason on the result

            elif hasattr(part, "text") and part.text:
                final_text += part.text

        if not has_function_call:
            break   # model produced a final text answer

    return final_text, tool_results

def _convert_messages(messages: list[dict]) -> list:
    """Convert frontend message format to Gemini contents."""
    contents = []
    for m in messages:
        role = "user" if m["role"] == "user" else "model"
        text = m.get("content") or ""
        if isinstance(text, list):
            text = " ".join(p.get("text", "") for p in text if p.get("type") == "text")
        contents.append(types.Content(role=role, parts=[types.Part(text=text)]))
    return contents
```

---

## 7. Multi-agent routing (Python)

```python
# backend/agents/catalog_agent.py
from backend.gemini_client import generate_with_tools, build_function_declarations
from backend.tools.catalog import (
    lookup_part, check_compatibility, get_installation_info, find_parts_by_symptom,
    LookupPartInput, CheckCompatibilityInput, InstallInfoInput, FindBySymptomInput,
)
from backend.prompts import CATALOG_AGENT_PROMPT

CATALOG_TOOLS = {
    "lookup_part": ("Get details for a part by PS number or MPN.", LookupPartInput),
    "check_compatibility": ("Check if a part fits a model. Returns boolean + appliance types.", CheckCompatibilityInput),
    "get_installation_info": ("Get installation steps for a part.", InstallInfoInput),
    "find_parts_by_symptom": ("Find parts that fix a symptom using exact + fuzzy search.", FindBySymptomInput),
}

async def run_catalog_agent(query: str, context: str, conn, embed_fn) -> dict:
    tool_declarations = build_function_declarations(CATALOG_TOOLS)

    async def execute_tool(name: str, args: dict):
        if name == "lookup_part":
            return await lookup_part(conn, LookupPartInput(**args))
        elif name == "check_compatibility":
            return await check_compatibility(conn, CheckCompatibilityInput(**args))
        elif name == "get_installation_info":
            return await get_installation_info(conn, InstallInfoInput(**args))
        elif name == "find_parts_by_symptom":
            return await find_parts_by_symptom(conn, FindBySymptomInput(**args), embed_fn)
        return {"error": f"unknown tool: {name}"}

    prompt = f"Context: {context}\n\nUser: {query}" if context else query
    text, tool_results = await generate_with_tools(
        system_prompt=CATALOG_AGENT_PROMPT,
        messages=[{"role": "user", "content": prompt}],
        tool_declarations=tool_declarations,
        execute_tool=execute_tool,
    )
    return {"text": text, "tool_results": tool_results}
```

```python
# backend/agents/repair_agent.py  (same pattern)
# backend/agents/order_agent.py   (same pattern)
```

```python
# backend/agents/orchestrator.py
from backend.gemini_client import client, FLASH
from backend.agents.catalog_agent import run_catalog_agent
from backend.agents.repair_agent import run_repair_agent
from backend.agents.order_agent import run_order_agent
from backend.prompts import ORCHESTRATOR_PROMPT
from google.genai import types

AGENT_MAP = {
    "catalogAgent": run_catalog_agent,
    "repairAgent": run_repair_agent,
    "orderAgent": run_order_agent,
}

async def classify_and_route(messages: list[dict], conn, embed_fn) -> dict:
    """
    Step 1: Ask the orchestrator LLM which agent to use.
    Step 2: Run that agent with the user's query.
    Step 3: Return the agent's text + tool_results.
    """
    last_user_msg = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")

    # Build conversation context from recent messages
    context = "\n".join(
        f"{m['role']}: {m['content']}" for m in messages[-6:]  # last 3 turns
    )

    # Ask orchestrator which agent to pick
    routing_response = client.models.generate_content(
        model=FLASH,
        contents=[types.Content(role="user", parts=[types.Part(text=f"""
Given this conversation:
{context}

Which agent should handle the latest user message?
Reply with ONLY one of: catalogAgent, repairAgent, orderAgent
""")])],
        config=types.GenerateContentConfig(
            system_instruction=ORCHESTRATOR_PROMPT,
        ),
    )
    agent_name = routing_response.text.strip()

    # Validate and default
    if agent_name not in AGENT_MAP:
        agent_name = "catalogAgent"

    # Run the selected agent
    agent_fn = AGENT_MAP[agent_name]
    result = await agent_fn(
        query=last_user_msg,
        context=context,
        conn=conn,
        embed_fn=embed_fn,
    )
    result["agent"] = agent_name
    return result
```

---

## 8. FastAPI endpoint with SSE streaming

This is where it all comes together. The endpoint receives messages, runs the guardrail,
routes to the right agent, and streams results as typed Server-Sent Events.

```python
# backend/main.py
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import asyncpg
import json
import os

from backend.guardrail import is_in_scope
from backend.agents.orchestrator import classify_and_route
from backend.gemini_client import embed
from backend.memory import save_session, load_session

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000"],
                   allow_methods=["*"], allow_headers=["*"])

pool: asyncpg.Pool = None

@app.on_event("startup")
async def startup():
    global pool
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"])

@app.post("/chat")
async def chat(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    session_id = body.get("session_id", "default")

    async def event_stream():
        async with pool.acquire() as conn:
            # 1) Guardrail
            if not await is_in_scope(messages):
                yield _sse("text", "I'm the PartSelect parts assistant — I can help "
                    "with refrigerator and dishwasher parts, compatibility, installation, "
                    "repairs, and order tracking. What can I help you find?")
                yield _sse("done", {})
                return

            # 2) Route to agent and execute
            yield _sse("tool_status", {"state": "routing", "message": "Finding the right specialist…"})

            result = await classify_and_route(messages, conn, embed)

            # 3) Stream tool results (for card rendering)
            for tr in result.get("tool_results", []):
                yield _sse("tool_result", {
                    "tool_name": tr["tool_name"],
                    "args": tr["args"],
                    "result": tr["result"],
                })

            # 4) Stream the agent's text response
            yield _sse("text", result.get("text", ""))

            # 5) Done
            yield _sse("done", {"agent": result.get("agent")})

            # 6) Persist for cross-session memory
            assistant_msg = {"role": "assistant", "content": result.get("text", "")}
            await save_session(conn, session_id, messages + [assistant_msg])

    return StreamingResponse(event_stream(), media_type="text/event-stream")

def _sse(event: str, data) -> str:
    """Format a single Server-Sent Event."""
    payload = json.dumps(data) if isinstance(data, (dict, list)) else json.dumps(data)
    return f"event: {event}\ndata: {payload}\n\n"
```

### SSE event protocol (what the frontend parses):

| Event | Data | Frontend action |
|---|---|---|
| `tool_status` | `{ state, message }` | Show "Finding the right specialist…" indicator |
| `tool_result` | `{ tool_name, args, result }` | Render ProductCard / CompatVerdict / InstallDrawer |
| `text` | `"string"` | Append to assistant message bubble |
| `error` | `{ message }` | Show error state |
| `done` | `{ agent }` | Mark message complete |

---

## 9. Guardrail (Python)

```python
# backend/guardrail.py
from backend.gemini_client import client, FLASH_LITE
from google.genai import types

async def is_in_scope(messages: list[dict]) -> bool:
    last = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    response = client.models.generate_content(
        model=FLASH_LITE,
        contents=[types.Content(role="user", parts=[types.Part(text=last)])],
        config=types.GenerateContentConfig(
            system_instruction=(
                "Classify the user message. Reply ONLY 'IN' or 'OUT'.\n"
                "IN = about refrigerator/dishwasher parts, compatibility, installation, "
                "repair for those two types, or PartSelect orders.\n"
                "OUT = anything else."
            ),
        ),
    )
    text = response.text.strip()
    return "IN" in text and "OUT" not in text
```

---

## 10. Memory (Python)

```python
# backend/memory.py
import json

async def load_session(conn, session_id: str) -> list[dict]:
    row = await conn.fetchrow(
        "select messages from chat_sessions where session_id = $1", session_id)
    return json.loads(row["messages"]) if row else []

async def save_session(conn, session_id: str, messages: list[dict]):
    await conn.execute("""
        insert into chat_sessions (session_id, messages, updated_at)
        values ($1, $2, now())
        on conflict (session_id) do update
          set messages = excluded.messages, updated_at = now()
    """, session_id, json.dumps(messages))
```

---

## 11. System prompts (Python)

```python
# backend/prompts.py
ORCHESTRATOR_PROMPT = """You are the PartSelect assistant orchestrator.
Your job is to classify each user message and pick ONE specialist agent.

AGENTS:
- catalogAgent: part lookups, compatibility checks, installation info, finding parts by symptom
- repairAgent: troubleshooting, diagnosis, repair guides, "my X isn't working"
- orderAgent: cart operations, order status tracking

Reply with ONLY the agent name."""

CATALOG_AGENT_PROMPT = """You are the PartSelect catalog specialist.
You help users find parts, check compatibility, get installation instructions,
and identify which parts fix specific symptoms.

RULES:
- Extract PS numbers (PS#######) and model numbers exactly from the query.
- For compatibility checks, ALWAYS use check_compatibility. Never guess.
- When check_compatibility returns compatible=false with mismatched appliance_types,
  clearly state: "This is a [type] part, but your model is a [type]."
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
- Link to PartSelect repair videos when available."""

ORDER_AGENT_PROMPT = """You are the PartSelect order specialist.
You help users manage their cart and track orders.

RULES:
- For add_to_cart, confirm part name and price before adding.
- For order status, relay current status and tracking info.
- If an order or part isn't found, provide the helpful hint from the error."""
```

---

## 12. Frontend (TypeScript — unchanged except the chat hook)

### 12.1 Custom `useAgentChat` hook (replaces `useChat`)

This is the only new frontend code. It consumes SSE from FastAPI and manages message +
tool-result state.

```tsx
// app/hooks/useAgentChat.ts
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
  agent?: string;
}

export function useAgentChat({ sessionId }: { sessionId: string }) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [status, setStatus] = useState<"idle" | "streaming">("idle");
  const [toolStatus, setToolStatus] = useState<string | null>(null);
  const idCounter = useRef(0);

  const sendMessage = useCallback(async (text: string) => {
    const userMsg: ChatMessage = {
      id: `msg-${++idCounter.current}`,
      role: "user",
      content: text,
    };
    const newMessages = [...messages, userMsg];
    setMessages(newMessages);
    setStatus("streaming");
    setToolStatus(null);

    const assistantId = `msg-${++idCounter.current}`;
    let assistantText = "";
    const toolResults: ToolResult[] = [];

    try {
      const resp = await fetch("http://localhost:8000/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          messages: newMessages.map(m => ({ role: m.role, content: m.content })),
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

        // Parse SSE events from buffer
        const lines = buffer.split("\n\n");
        buffer = lines.pop() || "";   // keep incomplete chunk

        for (const block of lines) {
          const eventMatch = block.match(/^event: (\w+)/);
          const dataMatch = block.match(/data: (.*)/s);
          if (!eventMatch || !dataMatch) continue;

          const eventType = eventMatch[1];
          const data = JSON.parse(dataMatch[1]);

          switch (eventType) {
            case "tool_status":
              setToolStatus(data.message);
              break;
            case "tool_result":
              toolResults.push(data);
              break;
            case "text":
              assistantText = data;
              break;
            case "done":
              break;
          }
        }
      }

      // Add completed assistant message
      setMessages(prev => [...prev, {
        id: assistantId,
        role: "assistant",
        content: assistantText,
        toolResults,
      }]);
    } catch (err) {
      setMessages(prev => [...prev, {
        id: assistantId,
        role: "assistant",
        content: "Sorry, something went wrong. Please try again.",
        toolResults: [],
      }]);
    } finally {
      setStatus("idle");
      setToolStatus(null);
    }
  }, [messages, sessionId]);

  return { messages, sendMessage, status, toolStatus };
}
```

### 12.2 Chat component (simplified — no more nested agent unwrapping)

```tsx
// app/components/Chat.tsx
"use client";
import { useAgentChat, ToolResult } from "../hooks/useAgentChat";
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
  const isEmpty = messages.length === 0;

  return (
    <div className={styles.container}>
      <header className={styles.header}>
        <img src="/ps-logo.svg" alt="PartSelect" className={styles.logo} />
        <span className={styles.badge}>Parts Assistant</span>
      </header>

      <div className={styles.messages}>
        {isEmpty && (
          <div className={styles.welcome}>
            <h2>Hi! I'm the PartSelect parts assistant.</h2>
            <p>I can help you find refrigerator and dishwasher parts, check
               compatibility, troubleshoot problems, and track orders.</p>
            <SuggestedPrompts prompts={SUGGESTIONS} onSelect={sendMessage} />
          </div>
        )}

        {messages.map((m) => (
          <div key={m.id} className={m.role === "user" ? styles.userRow : styles.assistantRow}>
            {/* Tool result cards — rendered BEFORE the text */}
            {m.toolResults?.map((tr, i) => (
              <ToolResultCard key={i} result={tr} />
            ))}
            {/* Text bubble */}
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
  if (tool_name === "lookup_part" && !data.error)
    return <ProductCard data={data} />;
  if (tool_name === "check_compatibility")
    return <CompatVerdict data={data} />;
  if (tool_name === "get_installation_info" && !data.error)
    return <InstallDrawer data={data} />;
  return null;
}
```

Note how much simpler this is than the nested-agent-unwrapping problem from before.
Because the Python backend flattens all tool results into the SSE stream, the frontend
doesn't need to dig into nested agent outputs — each tool result arrives as its own
top-level event.

### 12.3 Brand tokens, CSS Modules, and UI components
*(Identical to previous version — globals.css, ProductCard.module.css, CompatVerdict,
InstallDrawer, SuggestedPrompts, Composer. See §12 of the uploaded doc. These are
TypeScript/CSS and don't change.)*

---

## 13. Error handling

*(Same table as previous version — the error handling is defined by the Python tool
functions returning `{ error, hint }` dicts. See §13 of the uploaded doc.)*

---

## 14. End-to-end walkthroughs

*(Identical flow — only the execution layer changed from AI SDK to FastAPI + google-genai.
See §14 of the uploaded doc.)*

---

## 15. Evaluation plan (Python — pytest)

```python
# eval/test_suite.py
import pytest
from backend.tools.catalog import check_compatibility, CheckCompatibilityInput

@pytest.mark.asyncio
async def test_trap_query_cross_appliance(db_conn):
    """PS11752778 (fridge) vs WDT780SAEM1 (dishwasher) → incompatible."""
    result = await check_compatibility(
        db_conn, CheckCompatibilityInput(ps_number="PS11752778", model_number="WDT780SAEM1"))
    assert result["compatible"] is False
    assert result["part_appliance_type"] == "refrigerator"
    assert result["model_appliance_type"] == "dishwasher"

@pytest.mark.asyncio
async def test_compatible_same_type(db_conn):
    """PS11752778 (fridge) vs WRS322FDAM00 (fridge) → compatible."""
    result = await check_compatibility(
        db_conn, CheckCompatibilityInput(ps_number="PS11752778", model_number="WRS322FDAM00"))
    assert result["compatible"] is True

@pytest.mark.asyncio
async def test_part_not_found(db_conn):
    result = await lookup_part(db_conn, LookupPartInput(identifier="PS99999999"))
    assert result["error"] == "part_not_found"

@pytest.mark.asyncio
async def test_guardrail_in_scope():
    assert await is_in_scope([{"role": "user", "content": "Is PS11752778 compatible with my fridge?"}])

@pytest.mark.asyncio
async def test_guardrail_out_of_scope():
    assert not await is_in_scope([{"role": "user", "content": "Write me a poem about cats"}])
```

Full suite: 25+ queries covering compatibility (true + false + trap), symptoms, install,
orders, and out-of-scope refusal. Run with `pytest eval/`.

---

## 16–18. Extensibility, Scalability, LangGraph upgrade path

*(Unchanged — see §16–18 of the uploaded doc. The same principles apply regardless of
language: new agent = new Python module, new tool = new function, LangGraph upgrade
tripwires remain the same.)*

---

## 19. Repo structure

```
partselect-agent/
├── backend/                          # Python (FastAPI)
│   ├── main.py                        # FastAPI app + /chat endpoint (§8)
│   ├── guardrail.py                   # scope classifier (§9)
│   ├── gemini_client.py               # google-genai wrapper, embed() (§6)
│   ├── memory.py                      # session persistence (§10)
│   ├── prompts.py                     # all system prompts (§11)
│   ├── db.py                          # asyncpg pool setup
│   ├── agents/
│   │   ├── orchestrator.py            # classify + route (§7)
│   │   ├── catalog_agent.py           # catalog specialist
│   │   ├── repair_agent.py            # repair specialist
│   │   └── order_agent.py             # order specialist
│   ├── tools/
│   │   ├── catalog.py                 # lookup, compat, install, symptom (§5)
│   │   ├── repair.py                  # search_repair_guides
│   │   └── order.py                   # cart, order status
│   └── scraper/
│       ├── extract.py                 # parse product/model pages (§3)
│       └── load.py                    # upsert into Supabase (§3.1)
├── frontend/                         # TypeScript (Next.js)
│   ├── app/
│   │   ├── globals.css                # brand tokens
│   │   ├── page.tsx
│   │   └── components/
│   │       ├── Chat.tsx + .module.css
│   │       ├── ProductCard.tsx + .module.css
│   │       ├── CompatVerdict.tsx + .module.css
│   │       ├── InstallDrawer.tsx + .module.css
│   │       ├── SuggestedPrompts.tsx + .module.css
│   │       └── Composer.tsx + .module.css
│   └── hooks/
│       └── useAgentChat.ts            # custom SSE chat hook (§12.1)
├── db/
│   └── schema.sql                     # full schema (§4)
├── eval/
│   └── test_suite.py                  # pytest eval harness (§15)
├── requirements.txt                   # Python deps
├── package.json                       # frontend deps
└── .env
    # GEMINI_API_KEY=...
    # DATABASE_URL=postgres://...
```

---

## 20. Setup

```bash
# ── Python backend ──
cd backend
python -m venv venv && source venv/bin/activate
pip install fastapi uvicorn asyncpg google-genai pydantic httpx beautifulsoup4 pytest pytest-asyncio
# Seed the database
psql "$DATABASE_URL" -f ../db/schema.sql
python -m backend.scraper.load       # runs scrape + embed pipeline

# ── TypeScript frontend ──
cd ../frontend
npx create-next-app@latest . --ts --app   # decline Tailwind
npm install
# No AI SDK server packages needed — only the frontend
# No @ai-sdk/google, no @ai-sdk/react (we use the custom hook)

# ── Run ──
# Terminal 1:
cd backend && uvicorn backend.main:app --reload --port 8000
# Terminal 2:
cd frontend && npm run dev
```

---

## 21. What I'd build next

- **Streaming token-by-token** — current version streams tool results but returns text in
  one chunk. Gemini's `generate_content_stream()` can stream tokens; wire that to SSE
  `text_delta` events for character-by-character rendering.
- **Full scraper coverage** for all refrigerator + dishwasher parts.
- **Scheduled re-sync** job to keep prices and stock current.
- **Image-based part identification** — user uploads a photo, Gemini Vision identifies it.
- **Analytics** — most-asked symptoms, failed searches, conversion rate.
