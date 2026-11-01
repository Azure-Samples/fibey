# Fibey Field Ops

Fibey is a runnable demo of an agent that connects to inventory, work orders,
a knowledge base, and workflow skills through **one** endpoint: the
**Foundry Toolbox**. A separate status dashboard is deployed; using it through
browser automation requires additional Toolbox configuration.

## What the agent can do

- Look up fiber parts, SKUs, stock levels, and inventory locations
- View, create, and update work orders
- Retrieve splicing procedures, safety protocols, and troubleshooting guidance
- Follow skill-based workflows for field briefings and work order preparation
- Check network or service status when a browser automation tool is configured

## Architecture

![Fibey architecture in local and hosted modes: React UI exchanges chat and SSE with the gateway, which runs the agent in-process or calls its Foundry-hosted Responses API. The agent uses the Toolbox for inventory, work orders, knowledge retrieval, and skills.](docs/images/architecture-overview.svg)

[Editable Draw.io source](docs/images/architecture-overview.drawio)
(open in [draw.io](https://app.diagrams.net/)).
Uses [official Azure service icons](https://learn.microsoft.com/en-us/azure/architecture/icons/).

The UI sends chat requests to the FastAPI gateway and receives streaming text,
tool activity, citations, and errors. In **local** mode, the gateway runs the
agent in-process. The Azure deployment uses **hosted** mode: the gateway calls
the Foundry-hosted agent's Responses API. Both modes connect to the same
Toolbox for operational tools and workflow skills.

See [docs/architecture.md](docs/architecture.md) for the detailed hosted system
diagram, deployment boundaries, and component responsibilities.

Both modes use `SkillsProvider(McpSkillsSource(session))` to read skills from
the Toolbox through MCP Resources (SEP-2640), retaining `load_skill` activity
in the sidebar. Python Agent Framework 1.5.0 lacks a native MCP-skills source,
so this repository supplies the adapter. See
[docs/toolbox-integration.md](docs/toolbox-integration.md).

## Prerequisites

- Python 3.12+
- Node.js 20+
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- Azure CLI (`az`) for local bearer-token authentication and cloud deployment;
  Azure Developer CLI (`azd`) with the `azure.ai.agents` extension for deployment
- An Azure AI Foundry project with a deployed model, a configured Toolbox,
  and published skills

## Quickstart (local)

```bash
# 1) Install Python and UI dependencies
./scripts/setup.sh

# 2) Copy and edit environment variables
cp .env.example .env
# At minimum set FOUNDRY_PROJECT_ENDPOINT, FOUNDRY_MODEL, TOOLBOX_MCP_URL

# 3) Authenticate for local access to Foundry
az login

# 4) Start the gateway + UI
./scripts/start-dev.sh

# 5) (optional) In separate terminals, start the local toolbox backends
cd services/inventory-mcp     && uv sync && uv run python server.py
cd services/work-orders-api   && uv sync && uv run python server.py
cd services/status-dashboard/public && python -m http.server 8003
```

Open the UI at <http://localhost:5173>.

| Service | Local URL |
|---|---|
| UI | `http://localhost:5173` |
| Gateway | `http://localhost:8080` |
| Inventory MCP | `http://localhost:8001` |
| Work Orders API | `http://localhost:8002` |
| Status Dashboard | `http://localhost:8003` |

> **Toolbox URL gotcha:** `TOOLBOX_MCP_URL` should **not** include
> `?api-version=v1`. The agent code auto-appends it. The Toolbox MCP
> endpoint requires `api-version=v1` (not a date-based version).

## Deploy to Azure

`azd` deploys the agent to Foundry hosted agents, the UI, gateway, and
operational backend services to Azure Container Apps, and provisions AI Search
and blob storage:

```bash
az login
azd auth login
azd extension install azure.ai.agents
azd up
```

The postprovision hook sets up the FoundryIQ knowledge base; the postdeploy
hook publishes five skills from `skills/` and attaches them to the Toolbox.
To publish edited skills, run `./scripts/setup-toolbox.sh`, then
`azd deploy fibey-agent` to refresh the hosted skill cache. Toolboxes and
skills require preview feature headers.

See [docs/deployment.md](docs/deployment.md) for the full deployment guide,
including publishing, automated RBAC, preview caveats, and first-deployment
ordering. Hosted chat and skill loading were verified against an existing
environment; a fresh resource-group deployment has not been exercised.

## Documentation

| Doc | When to read it |
|---|---|
| [`docs/toolbox-integration.md`](docs/toolbox-integration.md) | MCP authentication, resource-based skills adapter, publishing, and preview gotchas |
| [`docs/architecture.md`](docs/architecture.md) | Full system diagram, components, streaming protocol, agent modes |
| [`docs/local-development.md`](docs/local-development.md) | All env vars, running individual services, testing the gateway API |
| [`docs/deployment.md`](docs/deployment.md) | Azure deployment via `azd`, hosted-agent deployment, knowledge base setup, RBAC |

## Project layout

```text
src/fibey/                # Python package: agent + gateway
skills/                   # SKILL.md publishing source; runtime reads Toolbox resources
services/
  inventory-mcp/          # MCP inventory server (port 8001)
  work-orders-api/        # FastAPI work-orders service (port 8002)
  status-dashboard/       # Static service-status dashboard (port 8003)
  foundry-iq-docs/        # Markdown source for the FoundryIQ knowledge base
ui/                       # React + TypeScript + Tailwind frontend
infra/                    # Bicep modules for Container Apps + AI Search + blob
scripts/                  # Local helpers, knowledge-base setup, Toolbox/skills publisher
docs/                     # Architecture, deployment, local-dev, integration docs
```

## Contributing

This project welcomes contributions and suggestions. See
[CONTRIBUTING.md](CONTRIBUTING.md) for details.

## License

Licensed under the MIT License — see [LICENSE](LICENSE).

## Trademarks

This project may contain trademarks or logos for projects, products, or
services. Authorized use of Microsoft trademarks or logos is subject to and
must follow [Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/legal/intellectualproperty/trademarks/usage/general).
Use of Microsoft trademarks or logos in modified versions of this project
must not cause confusion or imply Microsoft sponsorship. Any use of
third-party trademarks or logos is subject to those third-party's policies.
