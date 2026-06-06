# Expert Architecture Review — PartSelect Chat Agent

## The hidden test most people miss

Before anything else: **example query #2 is a trap.**

- PS11752778 is a **Refrigerator Door Shelf Bin** ($47.40, Whirlpool).
- WDT780SAEM1 is a **Whirlpool Dishwasher**.

The correct answer is "No — PS11752778 is a refrigerator part, and WDT780SAEM1 is a
dishwasher. They're incompatible." This is exactly the scenario the deterministic
compatibility check (SQL join returning false) is designed for, and exactly the scenario
where vector similarity would dangerously hallucinate a plausible-sounding "yes."

If your agent gets this wrong, nothing else matters. The evaluators chose these specific
IDs deliberately.

---

## Scorecard: current implementation vs. every success criterion

### ✅ Strong: Hybrid retrieval (deterministic + semantic)
This is the strongest architectural idea in the doc and the thing most applicants will get
wrong. The deterministic/semantic split is correct, well-justified, and directly maps to
the trap query above. Don't change it.

### ✅ Strong: Tool design
Clean typed tools with Zod, side-effect-explicit, grounded in the DB. The model can't
invent facts. This is the right pattern.

### ✅ Strong: Guardrail strategy
Cheap classifier before the main model. Two-layer defense. Cost-efficient. Good.

### ✅ Strong: Memory approach
Within-chat via message history, cross-session via Supabase persistence. Practical, honest
about tradeoffs, correctly identifies that LangGraph's checkpointer doesn't fundamentally
change the story.

### ⚠️ Weak: "Agentic architecture" presentation
The brief explicitly grades on "agentic architecture." A single streamText loop with tools
is genuinely agentic, but it LOOKS simple on a slide. The fix isn't LangGraph — it's the
**agents-as-tools** pattern. Three specialist agents (repair, catalog, order) behind a
router, each with a focused system prompt and tool subset. Same AI SDK, but the
architecture diagram now shows deliberate multi-agent routing, which scores higher and is
more extensible. (See §A below.)

### ❌ Missing: Data acquisition pipeline
This is the biggest gap. The doc describes a schema but never explains WHERE THE DATA
COMES FROM. For an e-commerce agent over a real catalog, this is critical.

PartSelect's product pages are richly structured. Each page has:
- PS number + manufacturer part number (MPN)
- Description, price, stock status
- Compatible models (this IS the compatibility matrix)
- "Fixes these symptoms" (maps symptoms → parts, enormously valuable)
- Customer Q&A (gold for RAG)
- Repair videos + install instructions
- Ratings and review count
- Related/commonly ordered together parts

The implementation needs a data ingestion section:
1. **Scrape** PartSelect product pages for refrigerator + dishwasher parts
2. **Parse** the structured fields into the relational tables
3. **Extract** "Fixes these symptoms" as a separate symptoms table (see §B)
4. **Chunk + embed** repair guide text and Q&A content for pgvector
5. **Build the compatibility matrix** from each product's "compatible models" list

Without this, the case study has a schema but no way to populate it.

### ❌ Missing: The "Fixes these symptoms" data
PartSelect already maps parts → symptoms on their site. This is EXACTLY the data the
repair RAG needs, and it's structured, not unstructured. The current schema has a
`repair_guides` table with free-text `symptom` and a vector embedding. But the real data
is a structured mapping:

    PS11752778 → ["Door won't open or close", "Ice maker not making ice",
                  "Ice maker won't dispense ice"]

This should be a dedicated table, AND also embedded for fuzzy symptom matching.
Both paths, deterministic + semantic, on the same data. (See §B.)

### ❌ Missing: Richer data model
The schema is too thin compared to what PartSelect actually provides. Missing:
- `symptoms` table (part→symptom mapping from "Fixes these symptoms")
- `part_cross_refs` (alternate part numbers — users often have the MPN, not the PS#)
- `reviews` / rating aggregates (customers ask "is this part any good?")
- `related_parts` (commonly bought together — upsell opportunity)
- `video_urls` on parts, not just install steps

### ⚠️ Weak: System prompt
Referenced but never written out. The system prompt is half the agent's behavior. It needs
to be in the doc, especially the instruction to never assert compatibility from memory and
the JSON card contract.

### ⚠️ Weak: Error handling
What happens when:
- Part not found (typo in PS number)
- Model not found (user gives a serial number instead of model number)
- No repair guides match a symptom
- Gemini rate limit hit (free tier: 1,500 RPD for Flash)
- User asks about a third appliance type (scope edge case)

The agent needs graceful fallbacks for all of these, and the doc should show them.

### ⚠️ Weak: Conversation design / empty state
The doc focuses on the backend but says little about what the user SEES first. A chat agent
needs:
- A welcome message explaining what it can do
- 3-4 suggested prompts ("Find a part for my fridge", "Check if a part fits my model",
  "Help me fix my dishwasher", "Track my order")
- Clear indication this is for refrigerator + dishwasher parts only

This is a UX criterion, and the brief grades on "design of your interface."

### ⚠️ Weak: Evaluation plan
The brief grades on "ability to answer user queries accurately." How do you PROVE accuracy?
Need at minimum:
- A test suite of 20-30 queries with expected tool calls and answers
- Compatibility checks that should return true AND false
- Cross-appliance queries (the trap query) that must return false
- Symptom queries with expected recommended parts
- Out-of-scope queries that must be refused

---

## The LangGraph verdict (definitive)

**No. Don't use LangGraph for this case study.** Here's why, in one paragraph:

The five criteria for needing a backend orchestration graph (pause/resume mid-task,
deterministic branching you don't trust the model with, complex cyclic exit conditions,
stateful multi-agent handoffs, node-level testability) score zero solid hits against the
brief's requirements. The three example queries are all single-turn tool calls or short
sequences the model picks naturally. The "agentic architecture" criterion is better served
by the agents-as-tools pattern (three specialist agents behind a router, all in the AI SDK)
than by a graph framework — because it shows architectural thinking AND simplicity, which
is exactly what "extensibility and scalability" means: easy to add agents, easy to add
tools, no framework lock-in. LangGraph's real value (durable checkpointed state, graph-
level testing, deterministic routing edges) is genuinely powerful but addresses problems
this case study doesn't have. Naming it as the documented upgrade path (with the specific
tripwire: "human-approval gates or resumable multi-session diagnosis") shows you KNOW the
framework and chose not to use it, which demonstrates stronger judgment than using it
by default.

---

## Required modifications (priority order)

### §A — Add multi-agent routing (agents-as-tools)

Replace the single `streamText` + all tools with three specialist agents, each exposed as a
tool to a lightweight orchestrator. This is the "agentic architecture" fix.

```
orchestrator (gemini-2.5-flash)
  ├── repairAgent     → search_repair_guides, lookup_part
  ├── catalogAgent    → lookup_part, check_compatibility, get_installation_info
  └── orderAgent      → add_to_cart, get_order_status
```

Each agent has its own focused system prompt. The orchestrator picks based on user intent.
They share the same message history for cross-agent context. This costs nothing in infra
(still pure AI SDK) but makes the architecture diagram substantive and the extensibility
story real ("add a new agent for a new domain").

### §B — Add the symptoms table and enrich the data model

```sql
create table symptoms (
  id             bigserial primary key,
  description    text not null,               -- "Ice maker not making ice"
  appliance_type text not null,
  embedding      vector(768)                  -- for fuzzy symptom matching
);

create table part_symptoms (
  part_id    bigint references parts(id),
  symptom_id bigint references symptoms(id),
  primary key (part_id, symptom_id)
);

-- Also add:
create table part_cross_refs (
  part_id   bigint references parts(id),
  alt_number text not null                    -- MPN, UPC, alternate PS#
);
create index on part_cross_refs (alt_number);
```

Now the repair flow can do BOTH:
1. Exact lookup: "ice maker not making ice" → part_symptoms join → candidate parts (fast,
   deterministic)
2. Fuzzy: "my fridge ice thing stopped working" → pgvector on symptoms.embedding → same
   candidates

This is the hybrid retrieval principle applied to symptoms, not just compatibility.

### §C — Add data acquisition section

At minimum, describe the pipeline:
1. Crawl PartSelect product pages for refrigerator + dishwasher categories
2. Parse structured fields (PS#, MPN, price, stock, description, compatible models,
   "Fixes these symptoms", rating, reviews)
3. Build the compatibility matrix from each product's compatible model list
4. Chunk + embed repair content and customer Q&A into the repair_guides table
5. Embed symptom descriptions for fuzzy matching

For the case study demo, seed with ~50-100 real parts covering the example queries.
In production, this becomes a scheduled crawler keeping the catalog fresh.

### §D — Write the actual system prompt

Include it in the doc. Key elements:
- Scope (refrigerator + dishwasher parts only)
- NEVER assert compatibility, price, or stock without calling a tool
- Extract PS numbers and model numbers from user input
- When a model number belongs to a different appliance type than the part, explicitly
  state the mismatch (the trap query)
- Return structured product data that the UI renders as cards
- For symptoms, ask for appliance type and model if not provided

### §E — Add conversation design

- Welcome message: "Hi! I'm PartSelect's parts assistant. I can help you find
  refrigerator and dishwasher parts, check compatibility, troubleshoot problems, and
  track orders. What can I help with?"
- 4 suggested prompts as tappable chips
- "Thinking" / tool-status indicators
- Empty-state design

### §F — Add error handling patterns

- Part not found → "I couldn't find a part with that number. Could you double-check?
  You can also try the manufacturer part number."
- Model not found → "That doesn't match a model in our system. Model numbers usually
  look like WDT780SAEM1. Could you check the tag inside your appliance door?"
- No repair guides → "I don't have specific repair steps for that issue, but here are
  the most commonly replaced parts for [appliance type] when [symptom]." Falls back to
  the symptoms table.
- Rate limit → queue with retry, or degrade to the lite model with an apology.

### §G — Add a basic evaluation plan

Even a small test suite shows rigor:
- 5 compatibility queries (3 true, 2 false including cross-appliance)
- 5 repair/symptom queries with expected parts
- 5 out-of-scope queries that must be refused
- 3 installation queries
- 2 order status queries

Run on every prompt/tool change. Report accuracy.

---

## Final summary

The core architecture (hybrid retrieval, typed tools, AI SDK loop, Gemini on free tier) is
correct and doesn't need to change. What it needs is:

1. **Richer agentic story** → agents-as-tools (§A)
2. **Real data model** → symptoms table, cross-refs, reviews (§B)
3. **Data pipeline** → how catalog gets populated (§C)
4. **Written system prompt** (§D)
5. **Conversation UX** → welcome, suggested prompts, empty state (§E)
6. **Error handling** (§F)
7. **Eval plan** (§G)

LangGraph: **no.** Document it as the upgrade path.

These seven additions turn a strong backend architecture into a complete, defensible case
study that scores on every criterion the brief lists.
