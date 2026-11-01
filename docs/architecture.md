# Architecture

## Overview

Fibey Field Ops is a fiber optics field operations demo built on **Azure AI Foundry Hosted Agents** and the **Foundry Toolbox**. The published Toolbox provides inventory lookup, work order management, knowledge retrieval, and five workflow skills. A separate status dashboard is deployed for service-status verification; browser automation is not registered by the current publisher.

## System Diagram

```
┌────────────────────────────────────────────────────────────────────────────┐
│ Browser                                                                    │
│  Fibey Field Ops UI                                                        │
│  ┌──────────────────────────────┬───────────────────────────────────────┐  │
│  │ Chat for field technicians   │ Activity sidebar                      │  │
│  │ "Need a splice tray and WO" │ ⚡ inventory-mcp running              │  │
│  │ "Check outage before cutover"│ ✅ work-orders-api complete          │  │
│  └──────────────────────────────┴───────────────────────────────────────┘  │
└───────────────────────────────┬────────────────────────────────────────────┘
                                │ POST /api/chat (streaming SSE)
                                ▼
                   ┌──────────────────────────────┐
                   │ Gateway (FastAPI)            │  src/fibey/gateway/
                   │ Azure Container Apps         │
                   └───────────────┬──────────────┘
                                   │ hosted Responses API (local: in-process)
                                   ▼
                   ┌──────────────────────────────┐
                   │ Fibey Field Ops Agent        │  src/fibey/agent/
                   │ Foundry hosted agent         │
                   └───────────────┬──────────────┘
                                   │ Toolbox MCP tools + skill resources
                                   ▼
        ┌─────────────────────────────────────────────────────────────────┐
        │ Toolbox services                                                │
        │  • inventory-mcp         → parts inventory and stock checks     │
        │  • work-orders-api       → create/view/update work orders       │
        │  • FoundryIQ knowledge   → AI Search + KB MCP retrieval         │
        │  • MCP Resources         → five workflow skills                 │
        └───────────────┬───────────────────────┬─────────────────────────┘
                        │                       │
          ┌─────────────▼────────────┐  ┌──────▼─────────────────────┐
          │ services/inventory-mcp   │  │ services/work-orders-api   │
          │ Container App / :8001    │  │ Container App / :8002      │
          └──────────────────────────┘  └────────────────────────────┘

          ┌──────────────────────────┐  ┌────────────────────────────┐
          │ services/status-dashboard│  │ services/foundry-iq-docs   │
          │ Static app / :8003       │  │ Blob → Search → KB source  │
          └──────────────────────────┘  └────────────────────────────┘
```

## Components

### Gateway (`src/fibey/gateway/`)

FastAPI API layer between the React UI and the field operations agent.

- **Deployment target**: Azure Container App
- **Endpoints**:
  - `POST /api/chat` — streaming SSE chat endpoint
  - `POST /api/sessions/reset` — clear session state
  - `GET /api/health` — health check
- **Responsibilities**: session management, SSE formatting, routing to local or hosted agent execution
- **Hosted flow**: authenticated streaming request to
  `<project-endpoint>/agents/fibey-agent/endpoint/protocols/openai/responses?api-version=2025-11-15-preview`.
  The gateway translates Responses events into the same UI SSE contract and
  preserves skill names, discovered operations, arguments, and tool errors.
- **History**: local `AgentSession`, or an in-memory per-session hosted
  response-ID mapping sent as `previous_response_id`. Reset clears the
  mapping; gateway restarts lose it.

### Agent (`src/fibey/agent/`)

The field operations agent that selects the right toolbox service for the technician's request.

- **Deployment target**: Foundry hosted agent (`azure.ai.agent` in `azure.yaml`)
- **Key files**:
  - `agent.py` — agent definition + Toolbox MCP connection
  - `hosted.py` — hosted-mode entrypoint
  - `mcp_skills.py` — MCP Resources adapter for `SkillsProvider`
  - `main.py` — local CLI entrypoint
  - `prompts/system_prompt.md` — field operations system prompt

Both modes use `MCPStreamableHTTPTool` with explicit per-request authentication
and `Foundry-Features: Toolboxes=V1Preview,Skills=V1Preview`. The hosted
entrypoint starts `ResponsesHostServer`; it is a separate deployable runtime
from the gateway.

### Skills

`skills/<name>/SKILL.md` is the git publishing source. The postdeploy hook
uploads versioned skills to Foundry and attaches unpinned skill references to
the Toolbox. Runtime discovery uses MCP Resources (SEP-2640):
`resources/list` finds `skill://index.json`; `resources/read` fetches the
index and skill instructions.

Python Agent Framework 1.5.0 lacks a native MCP-skills source (unlike C#
`UseMcpSkills`). `McpSkillsSource` supplies `InlineSkill` objects to
`SkillsProvider` in both modes, keeping `load_skill` visible in the activity
sidebar. Hosted startup preloads and caches skills; local runs create a new
source. Resource-loading failures propagate, while zero attached skills
produce a warning. See [toolbox-integration.md](toolbox-integration.md) for
details and [deployment.md](deployment.md) for publishing and restart steps.

### Frontend (`ui/`)

React + TypeScript + Tailwind chat experience for field technicians.

- **Deployment target**: Separate Azure Container App serving the static build
  with nginx and proxying `/api/` to the gateway
- **Key components**:
  - `ChatPanel` — conversation view
  - `MessageBubble` — markdown rendering
  - `ChatInput` — message entry
  - `PromptSuggestions` — clickable starter prompts shown in empty state
  - `ActivitySidebar` — live tool activity feed

### Toolbox services (`services/`)

The `services/` folder contains the operational systems the agent uses:

```text
services/
├── foundry-iq-docs/      # Markdown docs uploaded to blob storage for FoundryIQ
│   └── docs/
├── inventory-mcp/        # MCP server for parts inventory lookup
│   ├── data/
│   └── server.py
├── status-dashboard/     # Static HTML dashboard for network/service status
│   └── public/
└── work-orders-api/      # FastAPI service for work order operations
    ├── data/
    └── server.py
```

#### `services/inventory-mcp/`
- Streamable HTTP MCP server
- Default local port: `8001`
- Provides part search, detail lookup, and stock status checks

#### `services/work-orders-api/`
- FastAPI service for work order CRUD-style operations
- Default local port: `8002`
- Provides work order list, detail, create, and patch endpoints

#### `services/status-dashboard/`
- Static dashboard intended for browser automation for network/service checks;
  that tool requires additional Toolbox configuration
- Default local port: `8003`
- Packaged as a lightweight web container

#### `services/foundry-iq-docs/`
- Source markdown files for FoundryIQ knowledge retrieval
- Full retrieval pipeline: Documents → Blob Storage → AI Search Indexer → Index → Knowledge Source → Knowledge Base → MCP endpoint → Foundry connection
- Knowledge sources, knowledge bases, and KB MCP use `2026-05-01-preview`;
  Bicep creates a `RemoteTool` connection with `ProjectManagedIdentity` auth
- Uploaded to Azure Blob Storage separately from app deployment

## Container Apps topology

The deployment is organized around small, separable services:

| Component | Runtime | Port | Topology |
|-----------|---------|------|----------|
| UI | nginx | `80` | Azure Container App serving React and proxying `/api/` to the gateway |
| Gateway | FastAPI | `8000` deployed / `8080` local | Azure Container App handling chat API |
| Inventory MCP | Python MCP server | `8001` | Azure Container App exposed to the Toolbox |
| Work Orders API | FastAPI | `8002` | Azure Container App exposed to the Toolbox |
| Status Dashboard | Static web app | `8003` | Separate Azure Container App; browser tool not published automatically |
| Agent | Foundry hosted agent | `8088` in image | Foundry-managed Responses server; connects to Toolbox for tools and skills |
| FoundryIQ docs | Blob storage + Azure AI Search | n/a | Blob-backed content ingested by AI Search, then exposed through a knowledge base MCP endpoint |

The five Container Apps are UI, gateway, inventory, work orders, and status
dashboard. The agent image is built from `Dockerfile.agent` and runs in Foundry.

## Streaming Protocol

The gateway streams SSE events to the frontend:

```text
event: activity
data: {"tool": "inventory-mcp", "status": "running", "detail": "Checking stock for splice tray..."}

event: delta
data: {"content": "Stock is low at the north depot."}

event: citation
data: {"source": "fiber-splicing-procedures.md", "url": "..."}

event: done
data: [DONE]
```

| Event | Purpose |
|-------|---------|
| `delta` | Assistant text chunk |
| `activity` | Tool invocation status |
| `citation` | Source reference from knowledge tools |
| `error` | Error message |
| `done` | End of stream |

## Agent modes

Controlled by `AGENT_MODE`:

- **`local`** — the gateway runs the agent in-process for local development.
- **`hosted`** — the gateway proxies to a Foundry-hosted agent
  (`src/fibey/agent/hosted.py`) managed by the Azure AI Foundry platform.
  This is the deployed demo mode configured by `azd up`.

Both modes keep the same UI behavior and streaming contract. Unsupported
`AGENT_MODE` values cause the gateway to fail at startup.

Toolboxes, skills, hosted Responses, and the KB integration use preview
surfaces. The existing-environment hosted/UI flow was verified in
[#36](https://github.com/Azure-Samples/fibey/issues/36); a fresh resource-group
deployment has not been exercised. See the deployment guide for caveats.
