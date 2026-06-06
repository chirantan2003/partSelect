# PartSelect Chat Agent — Complete Implementation Guide

> A transactional chat agent for PartSelect's Refrigerator & Dishwasher parts catalog.
> **The LLM handles language, the database handles truth, and the seam between them is
> a small set of typed tools routed through specialist agents.**

---

## 0. Stack at a glance

| Layer | Choice | Why |
|---|---|---|
| Interface | Next.js (App Router) + TypeScript + CSS Modules | Streaming chat, brand-matched UI, scoped styles |
| Orchestration | **Vercel AI SDK** — `streamText` + agents-as-tools | Multi-agent routing, one loop, no framework lock-in |
| Truth engine | Supabase (PostgreSQL + `pgvector`) | Relational truth + semantic search in one engine |
| LLM | Gemini 2.5 Flash (agents) + Flash-Lite (guardrail) | Free tier via AI Studio, native function calling |
| Memory | Conversation history (in-chat) + Supabase (cross-session) | Free within a chat; one table for resume |

**LangGraph** is documented as the upgrade path (§18) for when flows need durable,
resumable state or deterministic compliance gates — not used here because the brief's
requirements don't trip those criteria.

---

## 1. Design principles

### 1.1 Language → Truth → UI
The model never invents facts. It interprets input, picks a tool, and synthesizes results
into prose + UI cards. Every fact (price, stock, compatibility, steps) comes from the
database. Extending = "add data + add a tool + optionally add an agent."

### 1.2 Deterministic vs. semantic — never confuse them
This is the most important architectural decision and it directly determines whether the
agent passes or fails the case study's example queries.

**The trap in example query #2:** PS11752778 is a *Refrigerator Door Shelf Bin* ($47.40,
Whirlpool). WDT780SAEM1 is a *Whirlpool Dishwasher*. The correct answer is "No — this
is a refrigerator part and your model is a dishwasher. They're incompatible."

If you answer this with vector similarity, the embedding for "Whirlpool refrigerator door
bin" and "Whirlpool dishwasher" are CLOSE in vector space (same brand, both kitchen
appliances). The model could return a confident "compatible" for a part that doesn't fit,
and the customer buys a $47 component they can't use. **Compatibility is a SQL join
returning a boolean, never an embedding.**

The two retrieval paths:

- **Deterministic (exact SQL).** Compatibility, price, stock, order status, symptom→part
  mapping from PartSelect's structured "Fixes these symptoms" data.
- **Semantic (pgvector RAG).** Free-text symptom descriptions → repair guides, Q&A
  content, fuzzy symptom matching.

Both paths exist. The agent picks per query by choosing a tool.

### 1.3 Stay in scope, cheaply
A dedicated **classify-first** call (Flash-Lite) gates every message before the agent runs.
Off-topic traffic never reaches the expensive model or tools.

---

## 2. Architecture overview

```
┌────────────────────────────────────────────────────────────────────┐
│  BROWSER  (Next.js, CSS Modules)                                    │
│                                                                     │
│  useChat() → message history, streaming, tool-part rendering        │
│  Welcome message + suggested prompt chips                           │
│  ProductCard · CompatVerdict · InstallDrawer · OrderStatus          │
└──────────▲──────────────────────────────────────┬──────────────────┘
           │  UI message stream                    │  POST /api/chat
           │  (text deltas + tool parts)           │  { messages, sessionId }
           │                                       ▼
┌──────────┴───────────────────────────────────────────────────────────┐
│  /api/chat  ROUTE HANDLER                                             │
│                                                                       │
│  1) guardrail: classify (Flash-Lite) ── OUT ──► polite refusal        │
│                    │ IN_SCOPE                                         │
│                    ▼                                                  │
│  2) orchestrator: streamText + 3 specialist agents as tools           │
│        ┌───────────┼───────────┐                                     │
│        ▼           ▼           ▼                                     │
│   catalogAgent  repairAgent  orderAgent                              │
│   ┌──────────┐ ┌──────────┐ ┌──────────┐                            │
│   │lookup    │ │search    │ │add_to    │                             │
│   │compat   │ │repair   │ │cart      │                              │
│   │install  │ │symptom  │ │order    │                               │
│   │symptom  │ │lookup   │ │status   │                               │
│   └──────────┘ └──────────┘ └──────────┘                            │
│        │           │           │                                     │
│        └───────────┴───────────┘                                     │
│                    │  tool.execute()                                  │
│                    ▼                                                  │
│  3) persist messages → Supabase (cross-session)                      │
└────────────────────┬─────────────────────────────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────────────────────────────┐
│  SUPABASE (PostgreSQL + pgvector)                                     │
│                                                                       │
│  Relational truth: parts, models, part_compatibility, symptoms,       │
│                    installation_steps, carts, orders                   │
│  Semantic search:  repair_guides.embedding, symptoms.embedding        │
└──────────────────────────────────────────────────────────────────────┘
```

Three specialist agents, one orchestrator, one guardrail — all in the Vercel AI SDK. No
graph framework needed. Adding a new domain (e.g., washer parts) = add an agent + data.

---

## 3. Data acquisition pipeline

The implementation is only as good as its data. PartSelect product pages are richly
structured, and the pipeline extracts that structure into the relational + vector stores.

### 3.1 What PartSelect pages contain

Each product page provides:
- **PS number** + manufacturer part number (MPN) + alternate part numbers
- **Description**, price, stock status
- **Compatible models** list (this IS the compatibility matrix)
- **"Fixes these symptoms"** (structured mapping: part → symptom list)
- **Customer Q&A** (real questions and expert answers — gold for RAG)
- **Repair videos** and install instructions
- **Ratings** and review count
- **Related / commonly ordered together** parts

Each model page provides:
- All parts that fit that model, grouped by section (door parts, liner parts, etc.)
- Model diagrams
- Model-specific repair Q&A

### 3.2 Ingestion pipeline

```
PartSelect site
     │
     ▼
┌─ Scraper (Playwright / Cheerio) ────────────────────────────────┐
│  1. Crawl /Refrigerator-Parts.htm and /Dishwasher-Parts.htm     │
│  2. For each product page, extract structured fields:           │
│     PS#, MPN, alt numbers, price, stock, description,           │
│     compatible models, symptoms, rating, video URLs             │
│  3. For each model page, extract parts list + Q&A               │
└─────────────────────────────────────┬───────────────────────────┘
                                      │
                                      ▼
┌─ Transformer ───────────────────────────────────────────────────┐
│  4. Normalize → relational rows (parts, models, compatibility,  │
│     symptoms, part_symptoms, cross_refs, install_steps)         │
│  5. Chunk repair guide text + Q&A content (500-token chunks     │
│     with 50-token overlap)                                      │
│  6. Embed chunks + symptom descriptions using Gemini embedding  │
│     model (text-embedding-004, 768 dimensions)                  │
└─────────────────────────────────────┬───────────────────────────┘
                                      │
                                      ▼
┌─ Loader ────────────────────────────────────────────────────────┐
│  7. Upsert into Supabase (idempotent on PS# / model#)          │
│  8. Build HNSW indexes on vector columns                        │
└─────────────────────────────────────────────────────────────────┘
```

For the case study demo, seed ~50-100 real parts covering the three example queries and
a spread of common refrigerator/dishwasher parts. In production, this becomes a scheduled
job keeping the catalog fresh.

### 3.3 Embedding strategy

- **Model:** Gemini text-embedding-004 (768 dimensions, free tier)
- **What gets embedded:**
  - Repair guide body text (chunked, 500 tokens, 50-token overlap)
  - Customer Q&A (each Q+A pair as one chunk)
  - Symptom descriptions (short, embedded whole)
- **What does NOT get embedded:** compatibility, price, stock, part numbers — these are
  exact lookups, never vector search.

---

## 4. Data model (Supabase / PostgreSQL + pgvector)

```sql
create extension if not exists vector;

-- ── Catalog (relational truth) ──────────────────────────────────────
create table appliance_models (
  id            bigserial primary key,
  model_number  text unique not null,          -- 'WDT780SAEM1'
  brand         text not null,                   -- 'Whirlpool'
  appliance_type text not null                   -- 'refrigerator' | 'dishwasher'
    check (appliance_type in ('refrigerator','dishwasher'))
);

create table parts (
  id             bigserial primary key,
  ps_number      text unique not null,           -- 'PS11752778'
  mpn            text,                           -- 'WPW10321304'
  name           text not null,                  -- 'Refrigerator Door Shelf Bin'
  description    text,
  price_cents    int not null,                   -- 4740 = $47.40
  in_stock       boolean not null default true,
  image_url      text,
  rating         numeric(2,1),                   -- 4.8
  review_count   int default 0,
  appliance_type text not null
    check (appliance_type in ('refrigerator','dishwasher')),
  video_url      text                            -- repair/install video
);

-- Cross-reference for alternate part numbers (MPN, UPC, old PS#)
create table part_cross_refs (
  part_id    bigint references parts(id),
  alt_number text not null,
  primary key (part_id, alt_number)
);
create index on part_cross_refs (alt_number);

-- Compatibility matrix — THE deterministic source of truth
create table part_compatibility (
  part_id  bigint references parts(id),
  model_id bigint references appliance_models(id),
  primary key (part_id, model_id)
);
create index on part_compatibility (model_id);

-- Symptoms — structured "Fixes these symptoms" from PartSelect pages
create table symptoms (
  id             bigserial primary key,
  description    text unique not null,           -- 'Ice maker not making ice'
  appliance_type text not null,
  embedding      vector(768)                     -- fuzzy symptom matching
);
create index on symptoms using hnsw (embedding vector_cosine_ops);

create table part_symptoms (
  part_id    bigint references parts(id),
  symptom_id bigint references symptoms(id),
  primary key (part_id, symptom_id)
);

-- Installation steps
create table installation_steps (
  id          bigserial primary key,
  part_id     bigint references parts(id),
  step_no     int not null,
  text        text not null,
  difficulty  text,                              -- 'Easy' | 'Medium' | 'Hard'
  est_minutes int,
  video_url   text
);

-- Repair guides + Q&A (semantic / RAG)
create table repair_guides (
  id             bigserial primary key,
  appliance_type text not null,
  brand          text,
  title          text,
  body           text not null,                  -- guide text or Q&A content
  source_url     text,                           -- link back to PartSelect
  likely_part_ids bigint[] default '{}',
  embedding      vector(768)
);
create index on repair_guides using hnsw (embedding vector_cosine_ops);

-- Related / commonly bought together
create table related_parts (
  part_id         bigint references parts(id),
  related_part_id bigint references parts(id),
  primary key (part_id, related_part_id)
);

-- ── Transactions + sessions ──────────────────────────────────────────
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
  id           text primary key,               -- 'ORD-10293'
  session_id   text,
  status       text not null,                  -- 'processing'|'shipped'|'delivered'
  tracking_url text
);
create table chat_sessions (
  session_id text primary key,
  messages   jsonb not null default '[]',
  updated_at timestamptz default now()
);
```

The compatibility check — the query that catches the trap:

```sql
select exists (
  select 1
  from part_compatibility pc
  join parts p            on p.id = pc.part_id  and p.ps_number    = $1
  join appliance_models m on m.id = pc.model_id and m.model_number = $2
) as compatible;
```

PS11752778 (refrigerator) × WDT780SAEM1 (dishwasher) → `false`. No row in the
compatibility matrix. The agent explains the appliance-type mismatch because the tool
result includes both `part.appliance_type` and `model.appliance_type`.

---

## 5. Tools

AI SDK tools: Zod `inputSchema` + `execute` returning structured data. Tool results
become UI cards directly — no separate structured-output call needed.

```ts
// lib/tools/catalog.ts
import { tool } from "ai";
import { z } from "zod";
import { sql } from "../db";

export const lookup_part = tool({
  description:
    "Get details for a part by PS number or manufacturer part number. " +
    "Returns price, stock, description, rating, appliance_type, image.",
  inputSchema: z.object({
    identifier: z.string().describe("PS number (e.g. PS11752778) or MPN"),
  }),
  execute: async ({ identifier }) => {
    // Try PS number first, then cross-refs
    const [p] = await sql`
      select * from parts
      where ps_number = ${identifier}
      union all
      select p.* from parts p
      join part_cross_refs cr on cr.part_id = p.id
      where cr.alt_number = ${identifier}
      limit 1`;
    if (!p) return { error: "part_not_found", identifier,
      hint: "Double-check the number. You can also try the manufacturer part number." };
    return p;
  },
});

export const check_compatibility = tool({
  description:
    "Deterministically check if a part fits a specific appliance model. " +
    "Returns a boolean from the compatibility matrix plus both appliance types " +
    "so the agent can explain cross-type mismatches.",
  inputSchema: z.object({
    ps_number: z.string(),
    model_number: z.string(),
  }),
  execute: async ({ ps_number, model_number }) => {
    const [part] = await sql`select id, appliance_type from parts where ps_number = ${ps_number}`;
    const [model] = await sql`select id, appliance_type from appliance_models where model_number = ${model_number}`;
    if (!part) return { error: "part_not_found", ps_number };
    if (!model) return { error: "model_not_found", model_number,
      hint: "Model numbers look like WDT780SAEM1. Check the tag inside your appliance door." };
    const [{ compatible }] = await sql`
      select exists (
        select 1 from part_compatibility
        where part_id = ${part.id} and model_id = ${model.id}
      ) as compatible`;
    return {
      compatible,
      ps_number, model_number,
      part_appliance_type: part.appliance_type,
      model_appliance_type: model.appliance_type,
    };
  },
});

export const get_installation_info = tool({
  description: "Get ordered installation steps, difficulty, and video for a part.",
  inputSchema: z.object({ ps_number: z.string() }),
  execute: async ({ ps_number }) => {
    const steps = await sql`
      select s.* from installation_steps s
      join parts p on p.id = s.part_id
      where p.ps_number = ${ps_number} order by s.step_no`;
    if (!steps.length) return { error: "no_install_info", ps_number,
      hint: "Installation info isn't available for this part yet." };
    return { ps_number, steps };
  },
});

export const find_parts_by_symptom = tool({
  description:
    "Find parts that fix a specific symptom. Uses BOTH exact symptom matching " +
    "and fuzzy vector search. Returns candidate parts with prices.",
  inputSchema: z.object({
    symptom: z.string(),
    appliance_type: z.enum(["refrigerator", "dishwasher"]),
    model_number: z.string().optional().describe("If provided, filters to compatible parts only"),
  }),
  execute: async ({ symptom, appliance_type, model_number }) => {
    const embedding = await embed(symptom);
    // Path 1: exact symptom match
    const exact = await sql`
      select p.* from parts p
      join part_symptoms ps on ps.part_id = p.id
      join symptoms s on s.id = ps.symptom_id
      where s.appliance_type = ${appliance_type}
        and s.description ilike ${'%' + symptom + '%'}`;
    // Path 2: fuzzy vector match on symptom descriptions
    const fuzzy = await sql`
      select s.description as matched_symptom, p.*,
             1 - (s.embedding <=> ${embedding}::vector) as score
      from symptoms s
      join part_symptoms ps on ps.symptom_id = s.id
      join parts p on p.id = ps.part_id
      where s.appliance_type = ${appliance_type}
      order by s.embedding <=> ${embedding}::vector
      limit 5`;
    // Merge, deduplicate, optionally filter by model compatibility
    return { exact_matches: exact, fuzzy_matches: fuzzy };
  },
});
```

```ts
// lib/tools/repair.ts
export const search_repair_guides = tool({
  description: "Semantic search over repair guides and Q&A for troubleshooting advice.",
  inputSchema: z.object({
    query: z.string(),
    appliance_type: z.enum(["refrigerator", "dishwasher"]),
    brand: z.string().optional(),
  }),
  execute: async ({ query, appliance_type, brand }) => {
    const embedding = await embed(query);
    return sql`
      select id, title, body, source_url, likely_part_ids,
             1 - (embedding <=> ${embedding}::vector) as score
      from repair_guides
      where appliance_type = ${appliance_type}
        and (${brand ?? null}::text is null or brand ilike ${brand})
      order by embedding <=> ${embedding}::vector
      limit 3`;
  },
});
```

```ts
// lib/tools/order.ts
export const add_to_cart = tool({
  description: "Add a part to the current session's cart. Returns updated cart summary.",
  inputSchema: z.object({
    ps_number: z.string(),
    qty: z.number().int().min(1).default(1),
  }),
  execute: async ({ ps_number, qty }) => {
    /* upsert into cart_items, return cart summary with total */
  },
});

export const get_order_status = tool({
  description: "Look up a PartSelect order by order ID.",
  inputSchema: z.object({ order_id: z.string() }),
  execute: async ({ order_id }) => {
    const [o] = await sql`select * from orders where id = ${order_id}`;
    if (!o) return { error: "order_not_found", order_id };
    return o;
  },
});
```

---

## 6. Multi-agent routing (agents-as-tools)

Three specialist agents, each with a focused persona, prompt, and tool subset. The
orchestrator picks the right one per message. This is the "agentic architecture" — real
multi-agent routing, zero extra infrastructure.

```ts
// lib/agents.ts
import { tool, generateText } from "ai";
import { google } from "@ai-sdk/google";
import { z } from "zod";
import * as catalogTools from "./tools/catalog";
import * as repairTools from "./tools/repair";
import * as orderTools from "./tools/order";

const flash = google("gemini-2.5-flash");

export const catalogAgent = tool({
  description:
    "Handles part lookups, compatibility checks, installation info, and finding " +
    "parts by symptom from the structured catalog. Use for: 'find part X', " +
    "'is X compatible with Y', 'how to install X', 'what part fixes [symptom]'.",
  inputSchema: z.object({ query: z.string(), context: z.string().optional() }),
  execute: async ({ query, context }) => {
    const result = await generateText({
      model: flash,
      system: CATALOG_AGENT_PROMPT,
      prompt: context ? `Context: ${context}\n\nUser: ${query}` : query,
      tools: {
        lookup_part: catalogTools.lookup_part,
        check_compatibility: catalogTools.check_compatibility,
        get_installation_info: catalogTools.get_installation_info,
        find_parts_by_symptom: catalogTools.find_parts_by_symptom,
      },
      maxSteps: 4,
    });
    return { text: result.text, toolResults: result.steps.flatMap(s => s.toolResults) };
  },
});

export const repairAgent = tool({
  description:
    "Handles troubleshooting and repair guidance using repair guides and Q&A. " +
    "Use for: 'my fridge is leaking', 'ice maker not working', 'how to fix X'. " +
    "Can also look up parts after diagnosing.",
  inputSchema: z.object({ query: z.string(), context: z.string().optional() }),
  execute: async ({ query, context }) => {
    const result = await generateText({
      model: flash,
      system: REPAIR_AGENT_PROMPT,
      prompt: context ? `Context: ${context}\n\nUser: ${query}` : query,
      tools: {
        search_repair_guides: repairTools.search_repair_guides,
        find_parts_by_symptom: catalogTools.find_parts_by_symptom,
        lookup_part: catalogTools.lookup_part,
      },
      maxSteps: 4,
    });
    return { text: result.text, toolResults: result.steps.flatMap(s => s.toolResults) };
  },
});

export const orderAgent = tool({
  description:
    "Handles cart operations and order tracking. Use for: 'add to cart', " +
    "'where is my order', 'check order ORD-XXX'.",
  inputSchema: z.object({ query: z.string(), context: z.string().optional() }),
  execute: async ({ query, context }) => {
    const result = await generateText({
      model: flash,
      system: ORDER_AGENT_PROMPT,
      prompt: context ? `Context: ${context}\n\nUser: ${query}` : query,
      tools: {
        add_to_cart: orderTools.add_to_cart,
        get_order_status: orderTools.get_order_status,
        lookup_part: catalogTools.lookup_part,   // needs part details for cart
      },
      maxSteps: 3,
    });
    return { text: result.text, toolResults: result.steps.flatMap(s => s.toolResults) };
  },
});
```

**Extensibility proof:** adding a "warranty agent" or "washer parts agent" = write one more
`tool({...})` with its own prompt and tool subset. The orchestrator discovers it
automatically.

---

## 7. The route handler (the entire backend)

```ts
// app/api/chat/route.ts
import { streamText, convertToModelMessages, stepCountIs } from "ai";
import { google } from "@ai-sdk/google";
import { isInScope } from "@/lib/guardrail";
import { catalogAgent, repairAgent, orderAgent } from "@/lib/agents";
import { saveSession } from "@/lib/memory";
import { ORCHESTRATOR_PROMPT } from "@/lib/prompts";

export async function POST(req: Request) {
  const { messages, sessionId } = await req.json();

  // 1) cheap guardrail — before any expensive work
  if (!(await isInScope(messages))) {
    const stream = new ReadableStream({
      start(ctrl) {
        ctrl.enqueue(new TextEncoder().encode(
          "I'm the PartSelect parts assistant — I can help with refrigerator and " +
          "dishwasher parts, compatibility, installation, repairs, and order tracking. " +
          "What can I help you find?"
        ));
        ctrl.close();
      },
    });
    return new Response(stream, { headers: { "Content-Type": "text/plain" } });
  }

  // 2) orchestrator routes to the right specialist agent
  const result = streamText({
    model: google("gemini-2.5-flash"),
    system: ORCHESTRATOR_PROMPT,
    messages: convertToModelMessages(messages),
    tools: { catalogAgent, repairAgent, orderAgent },
    stopWhen: stepCountIs(6),
    onFinish: async ({ response }) => {
      await saveSession(sessionId, [...messages, ...response.messages]);
    },
  });

  return result.toUIMessageStreamResponse();
}
```

---

## 8. System prompts

### 8.1 Orchestrator prompt

```ts
export const ORCHESTRATOR_PROMPT = `You are the PartSelect assistant orchestrator.
Your job is to route each user message to the right specialist agent.

AGENTS:
- catalogAgent: part lookups, compatibility checks, installation info, finding parts by symptom
- repairAgent: troubleshooting, diagnosis, repair guides, "my X isn't working" questions
- orderAgent: cart operations, order status tracking

RULES:
1. Pick ONE agent per turn. Pass the user's query and any relevant conversation context.
2. If a repair diagnosis leads to a part recommendation, repairAgent handles the full flow
   (it can look up parts internally).
3. Synthesize the agent's response into a helpful, conversational answer.
4. Include structured part data from tool results so the UI can render product cards.
5. NEVER invent part numbers, prices, compatibility, or stock status. Only state what
   the agent's tools returned.
6. When a compatibility check returns false AND the part and model have different
   appliance_types (e.g., refrigerator part vs dishwasher model), explicitly explain
   the appliance type mismatch — don't just say "incompatible."
7. If a tool returns an error (part_not_found, model_not_found), relay the error's hint
   to the user helpfully.`;
```

### 8.2 Catalog agent prompt

```ts
export const CATALOG_AGENT_PROMPT = `You are the PartSelect catalog specialist.
You help users find parts, check compatibility, get installation instructions,
and identify which parts fix specific symptoms.

You have these tools: lookup_part, check_compatibility, get_installation_info,
find_parts_by_symptom.

RULES:
- Extract PS numbers (PS#######) and model numbers exactly from the query.
- If the user gives a manufacturer part number instead of a PS number, lookup_part
  handles cross-reference lookups automatically.
- For compatibility checks, ALWAYS use the check_compatibility tool. Never guess.
- When check_compatibility returns compatible=false with mismatched appliance_types,
  clearly state: "This is a [type] part, but your model is a [type]."
- For "what part fixes X" questions, use find_parts_by_symptom.
- Always include the part's price, stock status, and rating in your response.`;
```

### 8.3 Repair agent prompt

```ts
export const REPAIR_AGENT_PROMPT = `You are the PartSelect repair specialist.
You diagnose appliance problems and recommend replacement parts.

You have these tools: search_repair_guides, find_parts_by_symptom, lookup_part.

RULES:
- Ask for the appliance type (refrigerator/dishwasher) and brand if not obvious.
- Use search_repair_guides for troubleshooting advice and diagnosis.
- Use find_parts_by_symptom to identify likely replacement parts.
- Use lookup_part to get full details (price, stock) for recommended parts.
- Present a diagnosis first, then recommend specific parts with prices.
- If the user provides a model number, mention they can check compatibility.
- Link to PartSelect repair videos when available (source_url from guides).`;
```

### 8.4 Order agent prompt

```ts
export const ORDER_AGENT_PROMPT = `You are the PartSelect order specialist.
You help users manage their cart and track orders.

You have these tools: add_to_cart, get_order_status, lookup_part.

RULES:
- For add_to_cart, confirm the part name and price before adding.
- For order status, relay the current status and tracking info.
- If an order or part isn't found, provide the helpful hint from the tool's error.`;
```

---

## 9. Guardrail (classify-first)

```ts
// lib/guardrail.ts
import { generateText } from "ai";
import { google } from "@ai-sdk/google";

export async function isInScope(messages: any[]): Promise<boolean> {
  const last = messages.at(-1);
  const content = typeof last?.content === "string"
    ? last.content
    : JSON.stringify(last?.parts);

  const { text } = await generateText({
    model: google("gemini-2.5-flash-lite"),
    system:
      "Classify the user message. Reply ONLY 'IN' or 'OUT'.\n" +
      "IN = about refrigerator parts, dishwasher parts, appliance repair for those " +
      "two types, part compatibility, installation help, or PartSelect orders.\n" +
      "OUT = anything else (other appliances, general chat, coding, advice, etc.).",
    prompt: content,
  });
  return /IN/.test(text) && !/OUT/.test(text);
}
```

---

## 10. Memory

### 10.1 Within-chat — free, via message history
`useChat` holds the full `messages` array and POSTs it every turn. The model sees the
whole conversation, so "is *this* part compatible with *my* model?" resolves naturally from
prior turns. No extra state management needed.

### 10.2 Efficiency
Short support chats (5-15 turns) are well within Gemini's context window. Mitigations for
longer chats, in order: (1) do nothing — it's fine; (2) window the last N messages + a
rolling summary; (3) Gemini context caching for stable prefixes.

### 10.3 Cross-session persistence

```ts
// lib/memory.ts
import { sql } from "./db";

export const loadSession = async (id: string) =>
  (await sql`select messages from chat_sessions where session_id = ${id}`)
    [0]?.messages ?? [];

export const saveSession = async (id: string, messages: unknown[]) =>
  sql`insert into chat_sessions (session_id, messages, updated_at)
      values (${id}, ${JSON.stringify(messages)}, now())
      on conflict (session_id) do update
        set messages = excluded.messages, updated_at = now()`;
```

---

## 11. Gemini integration

### 11.1 Model selection (free-tier, 2026)

| Role | Model | Rate |
|---|---|---|
| Guardrail | `gemini-2.5-flash-lite` | 1,500 RPD free |
| All three agents + orchestrator | `gemini-2.5-flash` | 1,500 RPD free |
| Embeddings | `text-embedding-004` | Free tier |
| (Upgrade path) | `gemini-2.5-pro` / `3.x` | Paid |

Production caveat: free-tier inputs may be used by Google to improve models. A real
deployment with customer/PII data should use a paid tier or Vertex AI.

### 11.2 Embedding helper

```ts
// lib/gemini.ts
import { GoogleGenAI } from "@google/genai";
const ai = new GoogleGenAI({ apiKey: process.env.GOOGLE_GENERATIVE_AI_API_KEY! });

export async function embed(text: string): Promise<number[]> {
  const res = await ai.models.embedContent({
    model: "text-embedding-004",
    content: text,
  });
  return res.embeddings[0].values;
}
```

---

## 12. Frontend

### 12.1 Brand tokens (CSS custom properties)

```css
/* app/globals.css */
:root {
  --ps-blue:    #003b5c;      /* PartSelect primary navy */
  --ps-teal:    #00799e;      /* secondary teal */
  --ps-accent:  #f5a623;      /* CTA orange */
  --ps-bg:      #f5f7fa;
  --ps-card:    #ffffff;
  --ps-text:    #1a1a1a;
  --ps-muted:   #6b7280;
  --ps-ok:      #1e8e5a;
  --ps-bad:     #c0392b;
  --ps-border:  #e2e6ec;
  --radius:     10px;
  --shadow:     0 1px 3px rgba(0,0,0,.06), 0 4px 14px rgba(0,0,0,.05);
  --font:       system-ui, -apple-system, 'Segoe UI', sans-serif;
}
* { box-sizing: border-box; margin: 0; }
body { font: 15px/1.5 var(--font); background: var(--ps-bg); color: var(--ps-text); }
```

### 12.2 Chat component

```tsx
// app/components/Chat.tsx
"use client";
import { useChat } from "@ai-sdk/react";
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

export function Chat({ sessionId, initialMessages }: Props) {
  const { messages, sendMessage, status } = useChat({
    api: "/api/chat",
    body: { sessionId },
    initialMessages,
  });

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
            <SuggestedPrompts prompts={SUGGESTIONS}
              onSelect={(p) => sendMessage({ text: p })} />
          </div>
        )}

        {messages.map((m) => (
          <div key={m.id} className={
            m.role === "user" ? styles.userRow : styles.assistantRow
          }>
            {m.parts.map((part, i) => {
              if (part.type === "text")
                return <div key={i} className={styles.bubble}>{part.text}</div>;
              
              if (part.state === "input-available")
                return <div key={i} className={styles.thinking}>Contacting specialist…</div>;

              if (
                part.type === "tool" &&
                (part.toolName === "catalogAgent" || part.toolName === "repairAgent" || part.toolName === "orderAgent") &&
                part.state === "result-available"
              ) {
                const agentOutput = part.result;
                return (
                  <div key={i} className={styles.agentExecutionBlock}>
                    {agentOutput.toolResults?.map((tr: any, idx: number) => {
                      if (tr.toolName === "lookup_part" && !tr.result.error)
                        return <ProductCard key={idx} data={tr.result} />;
                      if (tr.toolName === "check_compatibility")
                        return <CompatVerdict key={idx} data={tr.result} />;
                      if (tr.toolName === "get_installation_info" && !tr.result.error)
                        return <InstallDrawer key={idx} data={tr.result} />;
                      return null;
                    })}
                  </div>
                );
              }
              return null;
            })}
          </div>
        ))}
        {status === "streaming" && <div className={styles.typing}><span/><span/><span/></div>}
      </div>

      <div className={styles.inputArea}>
        <Composer onSend={(t) => sendMessage({ text: t })}
                  disabled={status === "streaming"} />
      </div>
    </div>
  );
}
```

### 12.3 Key UI components

**ProductCard** — part image, name, PS number, price, in-stock badge, rating, Add-to-Cart
button. CSS enter animation (`@keyframes rise`) + hover lift.

**CompatVerdict** — large green check or red X, part name, model number, and — critically —
when appliance types don't match, a clear explanation: "This is a refrigerator part, but
WDT780SAEM1 is a dishwasher."

**InstallDrawer** — expandable `<details>` element with CSS height transition. Shows steps,
difficulty badge, estimated time, and video link.

**SuggestedPrompts** — tappable chips that populate and send a message.

**Typing indicator** — three-dot CSS animation.

### 12.4 CSS approach
Plain CSS Modules, no Tailwind, no Framer Motion. Enter animations via `@keyframes`,
hover/expand transitions via `transition`. Components are scoped via `.module.css` files
colocated with their `.tsx`. This keeps dependencies minimal and styling debuggable.

---

## 13. Error handling

| Scenario | Tool returns | Agent says |
|---|---|---|
| PS number not found | `{ error: "part_not_found", hint }` | "I couldn't find that part number. Double-check it, or try the manufacturer part number (e.g. WPW10321304)." |
| Model number not found | `{ error: "model_not_found", hint }` | "That doesn't match a model in our system. Model numbers look like WDT780SAEM1 — check the tag inside your appliance door." |
| Cross-appliance incompatibility | `{ compatible: false, part_appliance_type, model_appliance_type }` | "PS11752778 is a refrigerator part, but WDT780SAEM1 is a dishwasher — they're not compatible. Want me to find dishwasher parts for your model instead?" |
| No repair guides match | Empty results | "I don't have specific repair steps for that issue, but here are the most commonly replaced parts for [appliance] when [symptom]." Falls back to `find_parts_by_symptom`. |
| No install info | `{ error: "no_install_info" }` | "Installation instructions aren't available for this part yet. The repair video might help — [link]." |
| Gemini rate limit (429) | Caught in try/catch | Retry with exponential backoff (3 attempts). If exhausted: "I'm temporarily unable to help — please try again in a minute." |
| User gives ambiguous input | Model reasoning | Agent asks ONE clarifying question: "Is that a refrigerator or a dishwasher?" |

Error objects always include a `hint` field so the agent can relay actionable guidance
without inventing its own.

---

## 14. End-to-end walkthroughs

### Query 1: "How can I install part number PS11752778?"
1. Guardrail → IN_SCOPE
2. Orchestrator → `catalogAgent`
3. Catalog agent calls `lookup_part("PS11752778")` → Refrigerator Door Shelf Bin, $47.40
4. Catalog agent calls `get_installation_info("PS11752778")` → steps
5. UI renders: ProductCard (image, price, rating) + InstallDrawer (steps, difficulty: Easy,
   est. 5 min, tool-free — just align and snap into place)

### Query 2: "Is this part compatible with my WDT780SAEM1 model?" (THE TRAP)
1. "This part" resolves from message history (PS11752778 from prior turn)
2. Orchestrator → `catalogAgent`
3. Catalog agent calls `check_compatibility("PS11752778", "WDT780SAEM1")`
4. Returns: `{ compatible: false, part_appliance_type: "refrigerator", model_appliance_type: "dishwasher" }`
5. Agent: "No — PS11752778 is a refrigerator part (Door Shelf Bin), but WDT780SAEM1 is a
   dishwasher. They're not compatible. Would you like me to find door shelf bins for your
   dishwasher model instead?"
6. UI renders: red CompatVerdict card with appliance-type mismatch explanation

### Query 3: "The ice maker on my Whirlpool fridge is not working. How can I fix it?"
1. Orchestrator → `repairAgent`
2. Repair agent calls `search_repair_guides({ query: "ice maker not working", appliance_type: "refrigerator", brand: "Whirlpool" })` → diagnosis steps
3. Repair agent calls `find_parts_by_symptom({ symptom: "ice maker not making ice", appliance_type: "refrigerator" })` → candidate parts
4. Repair agent calls `lookup_part` on top candidates → full details with prices
5. Agent streams: diagnosis, then recommended parts
6. UI renders: prose diagnosis + ProductCards for ice maker assembly, water inlet valve, etc.,
   each with Add-to-Cart CTA

---

## 15. Evaluation plan

### 15.1 Test suite (minimum 25 queries)

**Compatibility — true (5):**
- "Is PS11752778 compatible with WRS322FDAM00?" → true (both refrigerator)
- (4 more verified from PartSelect's compatible models lists)

**Compatibility — false (5):**
- "Is PS11752778 compatible with WDT780SAEM1?" → false, cross-appliance (THE TRAP)
- "Is [dishwasher part] compatible with [refrigerator model]?" → false
- (3 more with same-type but non-matching models)

**Symptom/repair (5):**
- "My Whirlpool fridge ice maker isn't working" → recommends ice maker parts
- "My dishwasher isn't draining" → recommends drain pump, filter
- (3 more covering common symptoms)

**Installation (3):**
- "How do I install PS11752778?" → steps + difficulty
- (2 more)

**Order/cart (2):**
- "Add PS11752778 to my cart" → confirms part + price
- "Where is order ORD-10293?" → status

**Out-of-scope — must refuse (5):**
- "Write me a Python script"
- "What's the weather today?"
- "Help me fix my washing machine" (out of scope: washers)
- "Tell me a joke"
- "Can you help me with my car?"

### 15.2 What to measure
- **Accuracy:** correct tool called, correct result returned, correct prose synthesis
- **Scope adherence:** 100% refusal rate on out-of-scope queries
- **Latency:** time-to-first-token, total response time
- **Error handling:** graceful degradation on not-found and rate-limit scenarios

Run on every prompt or tool change.

---

## 16. Extensibility

- **New appliance category (washers):** add `appliance_type` value, seed data, optionally
  add a `washerAgent`. Zero changes to existing agents or tools.
- **New capability (returns, warranty):** add a tool + an agent if warranted.
- **New surface (SMS, widget, Slack):** `/api/chat` is channel-agnostic.
- **Swap model:** change `google("gemini-2.5-flash")` to any AI SDK-supported provider.

## 17. Scalability

- **Stateless route** → horizontal scale on Vercel.
- **Postgres indexes** for relational load; HNSW for vector search, sub-100ms at catalog
  scale. If vector load grows, swap `search_repair_guides.execute` to a dedicated store —
  tool contract unchanged.
- **Caching:** memoize `lookup_part` and `check_compatibility` (catalog data changes rarely).
- **Model routing as cost lever:** Flash-Lite guardrail, Flash agents.

## 18. LangGraph upgrade path (documented, not used)

LangGraph earns its place when control flow and durable state must live in code rather than
in the model's turn-by-turn tool choices. The specific tripwires for this case study:

1. **Human-approval gates:** "Orders over $X require a person to confirm." This is a
   deterministic edge that cannot be trusted to prompt compliance.
2. **Resumable multi-session diagnosis:** user starts a repair flow, leaves, returns
   tomorrow, and the agent picks up at the exact diagnostic step — not just the message
   history, but the graph state (which symptom was confirmed, which parts were ruled out).
3. **Regulated compliance routing:** if PartSelect adds financial transactions with
   compliance checks, those must be graph edges, not LLM discretion.

Until one of these becomes a requirement, the AI SDK tool loop covers the full brief with
less code and fewer moving parts.

---

## 19. Repo structure

```
partselect-agent/
├── app/
│   ├── api/chat/route.ts              # route handler (§7)
│   ├── globals.css                     # brand tokens (§12.1)
│   ├── page.tsx                        # loads session, renders Chat
│   └── components/
│       ├── Chat.tsx + .module.css
│       ├── ProductCard.tsx + .module.css
│       ├── CompatVerdict.tsx + .module.css
│       ├── InstallDrawer.tsx + .module.css
│       ├── SuggestedPrompts.tsx + .module.css
│       └── Composer.tsx + .module.css
├── lib/
│   ├── agents.ts                       # 3 specialist agents (§6)
│   ├── guardrail.ts                    # classify-first (§9)
│   ├── memory.ts                       # load/save session (§10)
│   ├── gemini.ts                       # embed() helper (§11.2)
│   ├── prompts.ts                      # all system prompts (§8)
│   ├── db.ts                           # Supabase/postgres client
│   └── tools/
│       ├── catalog.ts                  # lookup, compat, install, symptom
│       ├── repair.ts                   # search_repair_guides
│       └── order.ts                    # cart, order status
├── db/
│   ├── schema.sql                      # full schema (§4)
│   └── seed.ts                         # scrape + embed pipeline (§3)
├── eval/
│   └── test-suite.ts                   # 25-query eval harness (§15)
└── .env.local
    # GOOGLE_GENERATIVE_AI_API_KEY=...  (AI Studio)
    # DATABASE_URL=postgres://...       (Supabase)
```

## 20. Setup

```bash
npx create-next-app@latest partselect-agent --ts --app
cd partselect-agent
npm i ai @ai-sdk/react @ai-sdk/google @google/genai zod postgres
# Set GOOGLE_GENERATIVE_AI_API_KEY and DATABASE_URL in .env.local
psql "$DATABASE_URL" -f db/schema.sql
npx tsx db/seed.ts          # runs the scrape + embed pipeline
npm run dev
```

## 21. What I'd build next

- **Full scraper coverage** for all refrigerator + dishwasher parts on PartSelect.
- **Scheduled re-sync** to keep prices and stock current.
- **Image-based part identification** — user uploads a photo of a broken part, Gemini
  Vision identifies it.
- **Proactive recommendations** — "Customers who bought this also needed [related part]."
- **Analytics dashboard** — most-asked symptoms, failed searches, conversion rate from
  recommendation → cart.
- **A/B test** the system prompt for accuracy improvements.
