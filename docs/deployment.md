# Deployment

## Overview

Fibey Field Ops supports two agent modes:

1. **Foundry Hosted Mode** (Production) - Uses Foundry's managed agent hosting
2. **Local Development Mode** - Runs the agent in-process with the gateway

This guide deploys the agent to **Foundry hosted agents** and the UI, gateway,
and operational backend services to **Azure Container Apps** using `azd`.

## Architecture

The deployment creates all resources in a single resource group (`rg-<env-name>`):

| Component | Azure Service | Source | Purpose |
|-----------|---------------|--------|---------|
| Chat UI | Container App | `ui/` | React frontend with activity sidebar |
| Gateway | Container App | `src/fibey/gateway/` | FastAPI proxy (hosted mode in production) |
| Hosted agent | Foundry hosted agent | `src/fibey/agent/hosted.py` | Field operations agent + Toolbox MCP |
| Work Orders API | Container App | `services/work-orders-api/` | Work order CRUD backend |
| Inventory MCP | Container App | `services/inventory-mcp/` | Inventory MCP server |
| Status Dashboard | Container App | `services/status-dashboard/` | Network/service status page |
| AI Foundry account + project | Microsoft Foundry | `infra/core/ai/` | Hosts the model deployment, hosted agent, and toolbox |
| Model deployment | Foundry | `azure.yaml` | `gpt-4.1-mini` (configurable) |
| Foundry Toolbox | Foundry (postprovision) | `scripts/setup-toolbox.sh` | Single MCP endpoint dispatching to backend tools |
| AI Search | Azure AI Search | `services/foundry-iq-docs/` | Knowledge base index + KB MCP endpoint |
| Container Registry | ACR | — | Docker image storage (apps + hosted agent) |
| Storage Account | Blob Storage | `services/foundry-iq-docs/` | Document storage |
| Log Analytics / App Insights | Workspace | — | Logging and monitoring |

## Prerequisites

- Azure subscription with Owner or Contributor + RBAC Admin roles
- Azure CLI (`az`) and Azure Developer CLI (`azd`)
- The azd AI agents extension: `azd extension install azure.ai.agents`
- Docker Desktop (for local image builds if needed)

## Quick Deployment

```bash
# 1. Clone repository
git clone https://github.com/Azure-Samples/fibey.git
cd fibey

# 2. Login
az login
azd auth login

# 3. Deploy everything
azd up
```

`azd up` performs the full deployment:

1. **Provision** — creates the resource group, AI Foundry account + project,
   model deployment, AI Search, storage, ACR, Container Apps environment, all
   six container apps, the Foundry knowledge-base connection, and all RBAC
   assignments (Bicep in `infra/`).
2. **Postprovision hook** — uploads FoundryIQ docs, builds the search index,
   and creates the knowledge source/base (`scripts/setup-knowledge-base.sh`).
3. **Deploy** — builds and pushes container images for all apps and deploys
   the hosted agent to the Foundry project.
4. **Postdeploy hook** — creates the Foundry Toolbox with tool search enabled
   (`scripts/setup-toolbox.sh`).

**Important:** Do NOT include `?api-version=v1` in the `TOOLBOX_MCP_URL`. The agent code automatically appends this.

## Environment variables

All values are produced as Bicep outputs and stored in the azd environment
automatically — no manual `azd env set` steps are required. Useful overrides:

| Variable | Default | Description |
|----------|---------|-------------|
| `FOUNDRY_MODEL` | `gpt-4.1-mini` | Model deployment name |
| `FOUNDRY_MODEL_CAPACITY` | `100` | Model deployment capacity (TPM, in thousands). Preserved across `azd up` runs |
| `TOOLBOX_NAME` | `fibey-toolbox` | Foundry Toolbox name |
| `KB_NAME` | `fibey-field-ops-kb` | Knowledge base name |
| `AZURE_SEARCH_INDEX` | `foundry-iq-docs-index` | Search index name |
| `AZURE_AI_PROJECT_NAME` | `fibey-project` | Foundry project name |
| `AZURE_AI_DEPLOYMENTS_LOCATION` | primary location | Region for the Foundry account/models |

## FoundryIQ Knowledge Base Setup

The deployed knowledge path is:

```text
services/foundry-iq-docs/docs/
→ Blob Storage container
→ AI Search indexer
→ AI Search index
→ Knowledge Source
→ Knowledge Base
→ MCP endpoint
→ Foundry connection
```

This pipeline is configured **automatically** by the azd postprovision hook
(`scripts/postprovision.sh` → `scripts/setup-knowledge-base.sh`):

1. **Documents uploaded to blob storage** from `services/foundry-iq-docs/docs/`.
2. **Search index and indexer created** — blob data source, `foundry-iq-docs-index`,
   semantic configuration `default`, and the indexer that ingests the markdown files.
3. **Knowledge Source created** — `fibey-field-ops-ks` (`kind: searchIndex`,
   `api-version=2026-04-01`) pointing at `foundry-iq-docs-index`.
4. **Knowledge Base created** — `fibey-field-ops-kb`, referencing `fibey-field-ops-ks`.
5. **Foundry connection** — `kb-fibey-field-ops-kb` is created by Bicep
   (`RemoteTool` + `ProjectManagedIdentity`, target = KB MCP endpoint).
6. **RBAC** — Bicep grants the Foundry project and account managed identities
   `Search Index Data Reader` on the search service.

To re-run manually: `./scripts/setup-knowledge-base.sh` (requires an azd environment).

### Deployed components

| Layer | Name | Resource Group / Scope | Notes |
|-------|------|-------------------------|-------|
| Search service | `<env>-search` | `<resource-group>` | Azure AI Search, Basic SKU |
| Search index | `foundry-iq-docs-index` | Search service | 8 text documents, semantic ranking only, no vectors |
| Semantic config | `default` | `foundry-iq-docs-index` | `titleField=metadata_storage_name`, `contentField=content` |
| Knowledge source | `fibey-field-ops-ks` | AI Search REST API | `kind: searchIndex` via `2026-04-01` |
| Knowledge base | `fibey-field-ops-kb` | AI Search REST API | References `fibey-field-ops-ks` |
| MCP endpoint | `https://<search-service>.search.windows.net/knowledgebases/fibey-field-ops-kb/mcp` | AI Search | Exposed by the knowledge base |
| Foundry connection | `kb-fibey-field-ops-kb` | AI Services account | `RemoteTool` + `ProjectManagedIdentity` |
| RBAC | `Search Index Data Reader` | AI Services managed identity | Required on the search service |

The knowledge base retrieval was validated with semantic `intents` requests against `fibey-field-ops-ks`, returning references and source data:

```json
{
  "intents": [{"search": "How do I splice a fiber optic cable?", "type": "semantic"}],
  "knowledgeSourceParams": [{"knowledgeSourceName": "fibey-field-ops-ks", "kind": "searchIndex", "includeReferences": true, "includeReferenceSourceData": true}]
}
```

> **Note:** `2026-04-01` is the GA API version used here for knowledge sources and knowledge bases.
>
> **Note:** Azure AI Foundry workspaces currently allow up to **120 connections**. Plan for cleanup or capacity management if your workspace is near the limit.

## Notes

- All Container Apps are configured with **minReplicas: 1** to avoid cold starts.
- The FoundryIQ documents are uploaded to blob storage and indexed separately — they are not part of the container deployment.
- The status dashboard can be set to internal-only ingress if browser automation is the only consumer.
- Infrastructure definitions live in `infra/` (Bicep). The Foundry Toolbox is created automatically by the postprovision hook (`scripts/setup-toolbox.sh`).

## RBAC (automated)

All managed identity permissions are assigned by Bicep during `azd provision`:

| Identity | Role(s) | Scope |
|----------|---------|-------|
| gateway Container App | Azure AI User (Foundry User), Cognitive Services User, Cognitive Services OpenAI User | AI Foundry account |
| Foundry project + account | Search Index Data Reader | AI Search service |
| AI Search | Storage Blob Data Reader | Storage account |
| Foundry project | AcrPull | Container registry |

### Foundry RBAC Roles (Reference)

**Note:** Foundry roles were recently renamed. Use role definition IDs (GUIDs) instead of role names:

| Role Name | Role ID (GUID) | Purpose |
|-----------|----------------|---------|
| Foundry User | `53ca6127-db72-4b80-b1b0-d745d6d5456d` | Basic Foundry access |
| Foundry Owner | `c883944f-8b7b-4483-af10-35834be79c4a` | Full Foundry management |
| Foundry Account Owner | `e47c6f54-e4a2-4754-9501-8e0985b135e1` | Account-level management |
| Foundry Project Manager | `eadc314b-1a2d-4efa-be10-5d325db5065e` | Project management |

**Current Implementation:** The Fibey Agent uses Cognitive Services roles (not Foundry-specific roles). This may change if Foundry Toolbox requires specific Foundry roles in future updates.

## Hosted Agent Deployment

The Foundry-hosted agent is deployed via `azd` using the `azure.ai.agent` host type.

### Prerequisites

```bash
# Install the azd AI agents extension
azd extension install azure.ai.agents

# Log in
azd auth login
az login
```

### Deploy

```bash
# From the repo root — builds Dockerfile.agent (remote build), deploys the
# agent to the Foundry project, and provisions the model deployment if needed
azd up

# Or deploy only the agent (skip Container Apps)
azd deploy fibey-agent
```

### Hosted agent environment variables

| Variable | Source | Description |
|----------|--------|-------------|
| `FOUNDRY_PROJECT_ENDPOINT` | Auto-injected | Foundry project endpoint URL |
| `FOUNDRY_MODEL` | Auto-injected | Model deployment name (from `azure.yaml`) |
| `TOOLBOX_MCP_URL` | `agent.yaml` (`${TOOLBOX_MCP_URL}` from the azd env, set by `azd provision`) | Toolbox MCP endpoint URL |

> **Note:** All `FOUNDRY_*` and `AGENT_*` env vars are reserved by the platform.
> Do not set them in `agent.yaml` — they are auto-injected from the deployment config.

### Local vs Hosted mode

| | Local Mode | Hosted Mode |
|---|-----------|-------------|
| **Entrypoint** | `src/fibey/agent/agent.py` | `src/fibey/agent/hosted.py` |
| **Server** | FastAPI gateway (`api_server.py`) | `ResponsesHostServer` |
| **History** | In-memory `AgentSession` | Managed by Foundry platform |
| **Toolbox auth** | Azure CLI credential | Bearer token from managed identity |
| **Skills** | `SkillsProvider` | `SkillsProvider` |

### Foundry Toolbox

The Toolbox lives in the same Foundry project and provides a single MCP
endpoint that dispatches to the backend tools. It is created automatically by
the postdeploy hook (`scripts/setup-toolbox.sh`):

| Tool | Type | Backend |
|------|------|---------|
| Tool Search | `toolbox_search_preview` | Toolbox built-in tool discovery |
| Work Orders | OpenAPI (anonymous) | Container App (`work-orders-api`) |
| Inventory | MCP | Container App (`inventory-mcp`) |
| Knowledge Base | MCP via `kb-fibey-field-ops-kb` connection | AI Search knowledge base (`fibey-field-ops-kb`) |

**Tool search** is enabled on the toolbox: the initial `tools/list` exposes
only `tool_search` and `call_tool`, and the agent discovers backend tools on
demand by searching their descriptions. This keeps the model's tool list small
as more tools are added.

The hook skips creation if the toolbox already exists. To publish a new
version (e.g., after Container App FQDNs change) — the script automatically
promotes the new version to default:

```bash
FORCE_TOOLBOX_UPDATE=1 ./scripts/setup-toolbox.sh
```

## Gateway Modes

The gateway supports two modes configured via the `AGENT_MODE` environment
variable. Unsupported values cause the gateway to fail at startup.

### 1. Foundry Hosted Mode (Production)
```bash
AGENT_MODE=hosted
HOSTED_AGENT_ENDPOINT=https://<account>.services.ai.azure.com/api/projects/<project>
HOSTED_AGENT_NAME=fibey-agent
```
- Uses Foundry's managed agent hosting
- No container management needed
- Requires hosted agent deployment in Foundry project
- Configured automatically for the gateway by `azd up`

### 2. Local Mode (Default for Local Development)
```bash
AGENT_MODE=local
```
- Agent runs in-process with gateway
- For local development and testing
- Not suitable for production

## Troubleshooting

### Toolbox MCP Connection Errors

**Symptom:** `400 BadRequest` from Toolbox MCP endpoint during initialization

**Solution:** The Toolbox MCP endpoint requires `api-version=v1` (not date-based versions). The agent code in `src/fibey/agent/agent.py` automatically appends `?api-version=v1` to the `TOOLBOX_MCP_URL`. Verify:

1. `TOOLBOX_MCP_URL` does NOT include `?api-version=...`
2. Agent code includes the URL modification in `_create_toolbox_mcp()` function

Test directly:
```bash
TOKEN=$(az account get-access-token --resource https://ai.azure.com --query accessToken -o tsv)
curl "https://<account>.services.ai.azure.com/api/projects/<project>/toolboxes/<name>/mcp?api-version=v1" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Foundry-Features: Toolboxes=V1Preview" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

### Authentication Errors

**Symptom:** `DefaultAzureCredential failed to retrieve a token`

**Solution:** Verify managed identity has required roles:
```bash
az role assignment list \
  --assignee "$AGENT_MI_ID" \
  --query "[].{role:roleDefinitionName,scope:scope}" \
  --output table
```

### Container App Logs

```bash
# Application logs
az containerapp logs show \
  --name <env>-gateway \
  --resource-group <resource-group> \
  --type console \
  --tail 100

# System logs
az containerapp logs show \
  --name <env>-gateway \
  --resource-group <resource-group> \
  --type system \
  --tail 50
```

### Redeploy the Hosted Agent

```bash
# Rebuild Dockerfile.agent and deploy to the Foundry project
azd deploy fibey-agent
```

## Verification

After deployment, test the stack:

```bash
# Test UI
curl https://<env>-ui.<env-subdomain>.azurecontainerapps.io/

# Test gateway health (mode should be hosted)
curl https://<env>-gateway.<env-subdomain>.azurecontainerapps.io/api/health

# Test end-to-end chat via gateway
curl -X POST https://<env>-gateway.<env-subdomain>.azurecontainerapps.io/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"What tools do you have?","session_id":"test-123"}'
```

## Cleanup

```bash
# Remove all deployed resources
azd down

# Or manually delete resource group
az group delete --name <resource-group> --yes --no-wait
```
