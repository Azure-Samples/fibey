#!/usr/bin/env bash
# Create (or update) the Foundry Toolbox that dispatches to the Fibey backend
# tools: inventory MCP server, work orders OpenAPI, and the FoundryIQ knowledge
# base MCP endpoint. Runs as part of the azd postprovision hook.
set -euo pipefail

# ─── Resolve configuration from the azd environment ────────────────────
env_value() {
  azd env get-value "$1" 2>/dev/null || echo ""
}

PROJECT_ENDPOINT="${FOUNDRY_PROJECT_ENDPOINT:-$(env_value FOUNDRY_PROJECT_ENDPOINT)}"
TOOLBOX_NAME="${TOOLBOX_NAME:-$(env_value TOOLBOX_NAME)}"
TOOLBOX_NAME="${TOOLBOX_NAME:-fibey-toolbox}"
INVENTORY_FQDN="${INVENTORY_FQDN:-$(env_value inventoryMcpFqdn)}"
WORK_ORDERS_FQDN="${WORK_ORDERS_FQDN:-$(env_value workOrdersApiFqdn)}"
KB_NAME="${KB_NAME:-$(env_value KB_NAME)}"
KB_NAME="${KB_NAME:-fibey-field-ops-kb}"
SEARCH_ENDPOINT="${AZURE_SEARCH_ENDPOINT:-$(env_value AZURE_SEARCH_ENDPOINT)}"
KB_CONNECTION_NAME="kb-${KB_NAME}"

if [ -z "$PROJECT_ENDPOINT" ] || [ -z "$INVENTORY_FQDN" ] || [ -z "$WORK_ORDERS_FQDN" ] || [ -z "$SEARCH_ENDPOINT" ]; then
  echo "Missing required values (FOUNDRY_PROJECT_ENDPOINT, inventoryMcpFqdn, workOrdersApiFqdn, AZURE_SEARCH_ENDPOINT)."
  echo "Run 'azd provision' first."
  exit 1
fi

KB_MCP_ENDPOINT="${SEARCH_ENDPOINT}/knowledgebases/${KB_NAME}/mcp"

echo ""
echo "Foundry project : $PROJECT_ENDPOINT"
echo "Toolbox         : $TOOLBOX_NAME"
echo "Inventory MCP   : https://${INVENTORY_FQDN}/mcp"
echo "Work Orders API : https://${WORK_ORDERS_FQDN}"
echo "Knowledge base  : $KB_MCP_ENDPOINT (connection: ${KB_CONNECTION_NAME})"
echo ""

TOKEN=$(az account get-access-token --scope "https://ai.azure.com/.default" --query accessToken -o tsv)

# ─── Skip if the toolbox already exists (unless forced) ────────────────
EXISTING_STATUS=$(curl -sS -o /dev/null -w "%{http_code}" \
  "${PROJECT_ENDPOINT}/toolboxes/${TOOLBOX_NAME}?api-version=v1" \
  -H "Authorization: Bearer ${TOKEN}")

if [ "$EXISTING_STATUS" = "200" ] && [ "${FORCE_TOOLBOX_UPDATE:-0}" != "1" ]; then
  echo "✓ Toolbox '${TOOLBOX_NAME}' already exists — skipping creation."
  echo "  Set FORCE_TOOLBOX_UPDATE=1 to publish a new version."
  exit 0
fi

# ─── Fetch the Work Orders OpenAPI spec and inject the server URL ──────
echo "=== Fetching Work Orders OpenAPI spec ==="
fetch_spec() {
  curl --fail-with-body -sS --max-time 30 "https://${WORK_ORDERS_FQDN}/openapi.json"
}
RAW_SPEC=""
for i in 1 2 3 4 5; do
  if RAW_SPEC=$(fetch_spec); then
    break
  fi
  echo "  Attempt ${i}/5 failed — retrying in 15s..."
  sleep 15
done
if [ -z "$RAW_SPEC" ]; then
  echo "Could not fetch the Work Orders OpenAPI spec. Is the work-orders-api service deployed?"
  exit 1
fi
WORK_ORDERS_SPEC=$(echo "$RAW_SPEC" | \
  python3 -c "
import json, sys
spec = json.load(sys.stdin)
spec['servers'] = [{'url': 'https://${WORK_ORDERS_FQDN}'}]
json.dump(spec, sys.stdout)
")
echo "✓ Spec fetched ($(echo -n "$WORK_ORDERS_SPEC" | wc -c | tr -d ' ') bytes)"

# ─── Create the toolbox version ────────────────────────────────────────
echo ""
echo "=== Creating toolbox version ==="
BODY=$(python3 -c "
import json, sys

work_orders_spec = json.loads(sys.argv[1])

body = {
    'description': 'Fibey Field Ops toolbox: inventory, work orders, and knowledge base.',
    'tools': [
        # Enables tool search (preview): other tools are discovered via the
        # tool_search tool instead of being listed upfront to the model.
        {
            'type': 'toolbox_search_preview',
        },
        {
            'type': 'mcp',
            'server_label': 'inventory',
            'server_url': 'https://${INVENTORY_FQDN}/mcp',
            'require_approval': 'never',
        },
        {
            'type': 'openapi',
            'openapi': {
                'name': 'work_orders',
                'spec': work_orders_spec,
                'auth': {'type': 'anonymous'},
            },
        },
        {
            'type': 'mcp',
            'server_label': 'knowledge_base',
            'server_url': '${KB_MCP_ENDPOINT}',
            'require_approval': 'never',
            'project_connection_id': '${KB_CONNECTION_NAME}',
        },
    ],
}
json.dump(body, sys.stdout)
" "$WORK_ORDERS_SPEC")

RESPONSE=$(curl --fail-with-body -sS -X POST \
  "${PROJECT_ENDPOINT}/toolboxes/${TOOLBOX_NAME}/versions?api-version=v1" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d "$BODY")

echo "$RESPONSE" | python3 -m json.tool | head -20

# ─── Promote the new version to default ────────────────────────────────
NEW_VERSION=$(echo "$RESPONSE" | python3 -c "import json,sys; print(json.load(sys.stdin)['version'])")
curl --fail-with-body -sS -X PATCH \
  "${PROJECT_ENDPOINT}/toolboxes/${TOOLBOX_NAME}?api-version=v1" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{\"default_version\": \"${NEW_VERSION}\"}" > /dev/null
echo "✓ Version ${NEW_VERSION} set as default"

echo ""
echo "✓ Toolbox '${TOOLBOX_NAME}' created (version ${NEW_VERSION}, tool search enabled)"
echo "  MCP endpoint: ${PROJECT_ENDPOINT}/toolboxes/${TOOLBOX_NAME}/mcp?api-version=v1"
