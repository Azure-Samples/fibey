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

KB_MCP_ENDPOINT="${SEARCH_ENDPOINT}/knowledgebases/${KB_NAME}/mcp?api-version=2026-05-01-preview"

echo ""
echo "Foundry project : $PROJECT_ENDPOINT"
echo "Toolbox         : $TOOLBOX_NAME"
echo "Inventory MCP   : https://${INVENTORY_FQDN}/mcp"
echo "Work Orders API : https://${WORK_ORDERS_FQDN}"
echo "Knowledge base  : $KB_MCP_ENDPOINT (connection: ${KB_CONNECTION_NAME})"
echo ""

TOKEN=$(az account get-access-token --scope "https://ai.azure.com/.default" --query accessToken -o tsv)

SKILLS_DIR="${SKILLS_DIR:-$(cd "$(dirname "$0")/.." && pwd)/skills}"
SKILL_NAMES=()
if [ -d "$SKILLS_DIR" ]; then
  for d in "$SKILLS_DIR"/*/; do
    [ -f "${d}SKILL.md" ] && SKILL_NAMES+=("$(basename "$d")")
  done
fi

# ─── Publish skills (idempotent: a new skill version only if content changed) ──
publish_skill() {
  local name="$1" file="$SKILLS_DIR/$1/SKILL.md"
  local base="${PROJECT_ENDPOINT}/skills/${name}"
  local status tmp_zip version
  status=$(curl -sS -o /dev/null -w "%{http_code}" "${base}?api-version=v1" \
    -H "Authorization: Bearer ${TOKEN}" -H "Foundry-Features: Skills=V1Preview")

  if [ "$status" = "200" ]; then
    tmp_zip=$(mktemp)
    curl --fail-with-body -sS "${base}/content?api-version=v1" \
      -H "Authorization: Bearer ${TOKEN}" -H "Foundry-Features: Skills=V1Preview" \
      -o "$tmp_zip"
    if python3 - "$tmp_zip" "$file" <<'PY'
import sys, zipfile
remote = zipfile.ZipFile(sys.argv[1]).read("SKILL.md")
sys.exit(0 if remote == open(sys.argv[2], "rb").read() else 1)
PY
    then
      rm -f "$tmp_zip"
      echo "  ✓ ${name}: unchanged"
      return
    fi
    rm -f "$tmp_zip"
  fi

  version=$(curl --fail-with-body -sS -X POST "${base}/versions?api-version=v1" \
    -H "Authorization: Bearer ${TOKEN}" -H "Foundry-Features: Skills=V1Preview" \
    -F "files=@${file};type=text/markdown" \
    | python3 -c "import json,sys; print(json.load(sys.stdin)['version'])")
  # The first version becomes the default automatically; later ones must be promoted.
  curl --fail-with-body -sS -X POST "${base}?api-version=v1" \
    -H "Authorization: Bearer ${TOKEN}" -H "Foundry-Features: Skills=V1Preview" \
    -H "Content-Type: application/json" \
    -d "{\"default_version\": \"${version}\"}" > /dev/null
  echo "  ✓ ${name}: published version ${version}"
}

echo "=== Publishing skills from ${SKILLS_DIR} ==="
for name in "${SKILL_NAMES[@]}"; do
  publish_skill "$name"
done

SKILLS_JSON=$(python3 -c "
import json, sys
print(json.dumps([{'type': 'skill_reference', 'name': n} for n in sys.argv[1:]]))
" "${SKILL_NAMES[@]}")

# Create a toolbox version from a JSON body and promote it to default.
publish_toolbox_version() {
  local response new_version
  response=$(curl --fail-with-body -sS -X POST \
    "${PROJECT_ENDPOINT}/toolboxes/${TOOLBOX_NAME}/versions?api-version=v1" \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "Foundry-Features: Skills=V1Preview" \
    -H "Content-Type: application/json" \
    -d "$1") || {
      echo "Toolbox version creation failed:" >&2
      echo "$response" | python3 -m json.tool >&2 || echo "$response" >&2
      exit 1
    }
  new_version=$(echo "$response" | python3 -c "import json,sys; print(json.load(sys.stdin)['version'])")
  curl --fail-with-body -sS -X PATCH \
    "${PROJECT_ENDPOINT}/toolboxes/${TOOLBOX_NAME}?api-version=v1" \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "Content-Type: application/json" \
    -d "{\"default_version\": \"${new_version}\"}" > /dev/null
  echo "✓ Toolbox '${TOOLBOX_NAME}' version ${new_version} set as default"
  echo "  MCP endpoint: ${PROJECT_ENDPOINT}/toolboxes/${TOOLBOX_NAME}/mcp?api-version=v1"
}

# ─── Toolbox already exists: only publish a new version if skills changed ──
EXISTING=$(curl -sS -w "\n%{http_code}" \
  "${PROJECT_ENDPOINT}/toolboxes/${TOOLBOX_NAME}?api-version=v1" \
  -H "Authorization: Bearer ${TOKEN}")
EXISTING_STATUS="${EXISTING##*$'\n'}"
EXISTING_BODY="${EXISTING%$'\n'*}"

if [ "$EXISTING_STATUS" = "200" ] && [ "${FORCE_TOOLBOX_UPDATE:-0}" != "1" ]; then
  DEFAULT_VERSION=$(echo "$EXISTING_BODY" | python3 -c "import json,sys; print(json.load(sys.stdin)['default_version'])")
  CURRENT=$(curl --fail-with-body -sS \
    "${PROJECT_ENDPOINT}/toolboxes/${TOOLBOX_NAME}/versions/${DEFAULT_VERSION}?api-version=v1" \
    -H "Authorization: Bearer ${TOKEN}" -H "Foundry-Features: Skills=V1Preview")

  if echo "$CURRENT" | python3 -c "
import json, sys
current = json.load(sys.stdin).get('skills') or []
wanted = json.loads(sys.argv[1])
sys.exit(0 if {s['name'] for s in current} == {s['name'] for s in wanted} else 1)
" "$SKILLS_JSON"; then
    echo "✓ Toolbox '${TOOLBOX_NAME}' already exists with the current skills — nothing to publish."
    echo "  Set FORCE_TOOLBOX_UPDATE=1 to rebuild the tools and publish a new version."
    exit 0
  fi

  echo ""
  echo "=== Attaching skills to existing toolbox (reusing tools from version ${DEFAULT_VERSION}) ==="
  BODY=$(echo "$CURRENT" | python3 -c "
import json, sys
current = json.load(sys.stdin)
json.dump({
    'description': current.get('description', ''),
    'tools': current['tools'],
    'skills': json.loads(sys.argv[1]),
}, sys.stdout)
" "$SKILLS_JSON")
  publish_toolbox_version "$BODY"
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
            'name': 'work_orders',
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
    'skills': json.loads(sys.argv[2]),
}
json.dump(body, sys.stdout)
" "$WORK_ORDERS_SPEC" "$SKILLS_JSON")

publish_toolbox_version "$BODY"
