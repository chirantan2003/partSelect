# Frontend — PartSelect Parts Assistant

The Next.js chat interface for the PartSelect Parts Assistant. Built with **TypeScript**, **pure CSS Modules**, and a custom SSE streaming hook that connects to the FastAPI backend.

---

## Directory Structure

```
frontend/
├── src/
│   ├── app/
│   │   ├── globals.css            # Design tokens + global resets
│   │   ├── layout.tsx             # Root layout (fonts, metadata)
│   │   ├── page.tsx               # Entry point — mounts <Chat />
│   │   └── components/
│   │       ├── Chat.tsx           # Main chat container + message list
│   │       ├── Chat.module.css
│   │       ├── ProductCard.tsx    # Part details (price, stock, rating)
│   │       ├── ProductCard.module.css
│   │       ├── CompatVerdict.tsx  # Green/Red compatibility result card
│   │       ├── CompatVerdict.module.css
│   │       ├── InstallDrawer.tsx  # Accordion installation steps
│   │       ├── InstallDrawer.module.css
│   │       ├── Composer.tsx       # Text input + send button
│   │       ├── Composer.module.css
│   │       ├── SuggestedPrompts.tsx  # Quick-action chips (empty state)
│   │       └── SuggestedPrompts.module.css
│   └── hooks/
│       └── useAgentChat.ts        # Custom SSE streaming hook
├── public/
│   └── ps-logo.svg                # PartSelect logo
├── next.config.ts
├── tsconfig.json
└── package.json
```

---

## Getting Started

```bash
# From the frontend/ directory
npm install
npm run dev
# → App available at http://localhost:3000
```

Make sure the backend is running on `http://localhost:8000` before opening the app.

---

## Design System (`globals.css`)

All brand colors and spacing are defined as CSS custom properties:

| Variable | Value | Usage |
|---|---|---|
| `--ps-blue` | `#003b5c` | Primary navy (header, headings) |
| `--ps-blue-light` | `#0a4f77` | Hover states on dark elements |
| `--ps-teal` | `#00799e` | Secondary teal (user message bubbles) |
| `--ps-accent` | `#f5a623` | CTA orange (send button, highlights) |
| `--ps-accent-hover` | `#e09217` | Send button hover |
| `--ps-bg` | `#f4f6f8` | Page background |
| `--ps-card` | `#ffffff` | Card / message bubble background |
| `--ps-border` | `#dde2e8` | Subtle borders |
| `--ps-text` | `#1a2332` | Primary text |
| `--ps-muted` | `#6b7a8d` | Secondary/muted text |
| `--ps-success` | `#1a7f4b` | In-stock / compatible green |
| `--ps-error` | `#c0392b` | Out-of-stock / incompatible red |

All components reference these variables — no hardcoded color values.

---

## Hook — `useAgentChat.ts`

The core state management hook. Manages the full conversation and connects to the backend SSE stream.

```typescript
const { messages, sendMessage, status, toolStatus } = useAgentChat({ sessionId });
```

### State

| Property | Type | Description |
|---|---|---|
| `messages` | `ChatMessage[]` | Full conversation history |
| `status` | `"idle" \| "streaming"` | Whether a response is in-flight |
| `toolStatus` | `string \| null` | Current tool status text (e.g. "Finding the right specialist...") |

### `ChatMessage` shape

```typescript
interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;          // Natural-language text
  toolResults?: ToolResult[]; // Structured tool data → rendered as cards
}
```

### SSE Stream Parsing

The hook reads the SSE response body using the Fetch Streams API:

| SSE Event | Action |
|---|---|
| `tool_status` | Updates `toolStatus` state (shows "thinking" text) |
| `tool_result` | Appends to `toolResults[]` on the assistant message → triggers card render |
| `text` | Sets the `content` of the assistant message |
| `done` | Clears `toolStatus`, sets `status → "idle"` |

Conversation history is sent with every request so the backend has multi-turn context.

---

## Components

### `Chat.tsx` — Main Container

The top-level layout. Renders the header, scrollable message list, and the input area.

- **Empty state:** Shows a welcome message + `<SuggestedPrompts>` chips when `messages.length === 0`
- **Message list:** Maps each message to a user bubble (right-aligned, teal) or an assistant block (left-aligned, white card)
- **Tool results:** Each `toolResult` in an assistant message is passed to `<ToolResultCard>`, which picks the right component based on `tool_name`
- **Typing indicator:** Three animated dots shown while `status === "streaming"` and no `toolStatus` is set
- **Tool status:** A pulsing "thinking" label shown while the agent is selecting tools

### `ProductCard.tsx` — Part Details

Rendered when `tool_name === "lookup_part"` returns a successful result.

| Field Displayed | Source |
|---|---|
| Part name | `result.name` |
| PS Number | `result.ps_number` |
| Price | `result.price_cents / 100` (formatted as `$XX.XX`) |
| Stock badge | `result.in_stock` → green "In Stock" / red "Out of Stock" |
| Star rating | `result.rating` (filled stars) |
| Review count | `result.review_count` |
| Part image | `result.image_url` (placeholder if missing) |
| Add to Cart button | Dispatches `add_to_cart` intent |

### `CompatVerdict.tsx` — Compatibility Result

Rendered when `tool_name === "check_compatibility"` returns a result.

- **Green card** → `result.compatible === true`
- **Red card** → `result.compatible === false`
- When the appliance types differ (e.g. `part_appliance_type: "refrigerator"` vs `model_appliance_type: "dishwasher"`), an explicit cross-type mismatch explanation is shown.

### `InstallDrawer.tsx` — Installation Steps

Rendered when `tool_name === "get_installation_info"` returns a result. Uses an HTML5 `<details>` accordion.

| Displayed | Source |
|---|---|
| Difficulty badge | `steps[0].difficulty` (Easy / Medium / Hard) |
| Estimated time | `steps[0].est_minutes` |
| Numbered steps | `steps[].text` |
| Video link | `steps[0].video_url` (if present) |

### `SuggestedPrompts.tsx` — Quick-Action Chips

Shown only on the empty state (before the first message). Renders a grid of clickable prompt chips:

- "Find a part for my refrigerator"
- "Check if a part fits my model"
- "My dishwasher isn't draining"
- "Track my order"

Clicking a chip calls `sendMessage(prompt)` directly.

### `Composer.tsx` — Input Area

A textarea + send button. Features:
- **Disabled** while `status === "streaming"` (prevents double-sends)
- **Enter to send** (Shift+Enter for newline)
- Auto-focuses on mount

---

## Adding a New Tool Card

When a new backend tool is added, render its result in the frontend by:

1. Add a new component `MyNewCard.tsx` + `MyNewCard.module.css` in `components/`
2. Import it in `Chat.tsx`
3. Add a case in the `ToolResultCard` function:
   ```typescript
   if (tool_name === "my_new_tool" && !data.error) return <MyNewCard data={data} />;
   ```

No other changes needed.

---

## 💬 Testing UI Components with Prompts

You can test each interactive component on the frontend by typing these specific prompts in the chat:

### 1. Triggering `<ProductCard>`
* **Prompt:** `Can you tell me the price and stock status of part number PS10065979?`
* **UI Behavior:** Renders a clean white card with:
  * Part Image placeholder
  * Name: *"Upper Rack Adjuster Kit - White Wheels..."*
  * Pricing: *"$55.29"*
  * Badge: *"In Stock"* (Green)
  * Ratings: 5 stars
  * CTA: *"Add to Cart"* button (Orange)

### 2. Triggering `<CompatVerdict>`
* **Prompt:** `Is the refrigerator door bin PS11752778 compatible with my WDT780SAEM1 dishwasher?`
* **UI Behavior:** Renders a Red Warning card with an alert icon showing the message:
  * Title: *"Incompatible / Mismatch"*
  * Content: *"This is a refrigerator part, but your model WDT780SAEM1 is a dishwasher."*

### 3. Triggering `<InstallDrawer>`
* **Prompt:** `How do I install the dishwasher heating element PS8260087?`
* **UI Behavior:** Renders a collapsible accordion drawer below the product card:
  * Shows difficulty level (e.g. *Medium*) and estimated time (e.g. *15 mins*).
  * Expands to show numbered, sequential steps.

