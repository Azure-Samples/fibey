---
name: inventory-lookup
description: Handle inventory and parts queries — stock checks, part lookups, availability, category browsing, and equipment searches. Use when the technician asks about parts, stock levels, SKUs, availability, supplies, or equipment.
---

# Inventory Lookup

Use this skill when the technician asks about parts, stock, equipment, or availability.

## When to Use

- "Do we have any SC connectors?"
- "What's the stock on FIB-042?"
- "Show me all splice equipment"
- "Is the OTDR available?"
- "What connectors do we carry?"
- "Check stock for the parts on WO-007"

## Tools Used by This Skill

All inventory capabilities live in the Foundry Toolbox under the `inventory` MCP server, so the actual tool names are prefixed `inventory___`.

| Capability | Prefixed tool name |
|---|---|
| Free-text part search | `inventory___search_parts` |
| Browse / filter list | `inventory___list_parts` |
| Single-part details | `inventory___get_part_details` |
| Single-part stock | `inventory___check_stock` |
| Multi-part stock | `inventory___check_stock_batch` |

### Step 0: Discover tools (REQUIRED before first inventory call this turn)

If the inventory tools aren't already visible in your tool list, call `tool_search` **once** with **`limit: 10`**. Describe both the capability and the technician's domain terms so discovery requests rank the catalog search tool above stock tools that require known IDs.

Examples:
- "OTDR stock?" → `tool_search({"query": "search inventory catalog for OTDR models and stock", "limit": 10})`
- "do we have SC connectors?" → `tool_search({"query": "search inventory catalog for SC connectors", "limit": 10})`
- generic "check parts" → `tool_search({"query": "inventory parts stock", "limit": 10})`

Always pass `limit: 10` so all inventory tools surface. The returned tools stay callable for the rest of the turn.

If a free-text discovery request does not return `search_parts`, refine discovery once with `tool_search({"query": "search inventory catalog by equipment name and description", "limit": 10})`. Do not invoke an undiscovered tool, and do not substitute `check_stock` or `check_stock_batch`; those require real part IDs. If `search_parts` is still unavailable, report that live inventory search could not be retrieved rather than claiming there are no matching parts.

### Step 1: Choose the Right Tool

| Question Type | Tool (prefixed name) | Example |
|--------------|-------------|---------|
| Free-text search ("do we have…", "find…") | `inventory___search_parts` | "do we have splice trays?" |
| Browse by category | `inventory___list_parts` with `category` filter | "show all connectors" |
| Specific part by ID | `inventory___get_part_details` with `part_id` | "details on FIB-012" |
| Stock level for a known part | `inventory___check_stock` with `part_id` | "how many FIB-042 in stock?" |
| Stock levels for multiple parts | `inventory___check_stock_batch` with `part_ids` list | "check stock for FIB-003 and FIB-012" |

Invoke each tool via `call_tool` with `{"name": "<prefixed_name>", "arguments": {...}}`.

`search_parts` results already include stock quantity, status, location, price, and manufacturer. For model-availability questions, format that result directly without follow-up stock calls.

Before answering, inspect the returned `count` and `parts` fields:
- If `count > 0`, report every returned part and copy its stock values exactly.
- Say no models are available only when a successful result explicitly has `count: 0` and `parts: []`.
- Treat errors, missing fields, or malformed content as retrieval failures, not as zero inventory.

**Categories available:** Connectors, Cables, Splitters, Splice Equipment, Test Equipment

### Step 2: Format the Response

**For a single part:**
```
**FIB-042 — SC/APC Connector** 🟢 In Stock
- **SKU:** CONN-SC-APC-500
- **Stock:** 342 units (Warehouse A)
- **Price:** $4.50/unit
- **Manufacturer:** Corning
```

**For multiple parts (2+), ALWAYS use a table with status indicators:**

```
### Inventory Results

| Part | Stock | Status | Location |
|------|-------|--------|----------|
| SC Connector (FIB-012) | 342 | 🟢 In Stock | WH-A1 |
| LC Connector (FIB-015) | 12 | 🟡 Low Stock | WH-B2 |
| Splice Tray (FIB-023) | 0 | 🔴 Out of Stock | — |

> 🟡 **Note:** LC Connectors are running low — consider reordering.
```

**CRITICAL:** When checking multiple items, always use a markdown table. Never
list items as a plain paragraph. Separate different topics with `---` dividers
or `###` headers.

### Step 3: Interpret Stock Status

Always show a status indicator:
- 🟢 **In Stock** — quantity is above minimum threshold
- 🟡 **Low Stock** — quantity is at or below minimum threshold but > 0. Add: _"Stock is running low — consider reordering."_
- 🔴 **Out of Stock** — quantity is 0. Add: _"Currently unavailable. Check with supply chain for restock ETA."_

### Step 4: Provide Actionable Next Steps

- If stock is low or out: suggest reordering or checking alternatives
- If the technician seems to be prepping for a job: offer to check a work order's full parts list
- If they searched broadly: ask if they need details on a specific part

## What NOT to Do

- ❌ Do not guess stock quantities — always use the inventory tools
- ❌ Do not skip the stock status indicator
- ❌ Do not use knowledge base tools for inventory questions
- ❌ Do not invent part IDs or SKUs
- ❌ Do not list multiple items as a flat paragraph — always use tables
- ❌ Do not call stock tools without part IDs from the user, a work order, or `search_parts`
- ❌ Do not repeat the same tool call; correct one validation error at most, then stop
