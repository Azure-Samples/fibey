#!/usr/bin/env bash
# Upload FoundryIQ docs to blob storage and configure the AI Search pipeline:
# data source → index → indexer → knowledge source → knowledge base.
#
# The Foundry project connection (kb-<kb-name>) and Search RBAC assignments are
# created by the Bicep templates in infra/. Runs as part of the azd
# postprovision hook; safe to re-run (all operations are idempotent PUTs).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DOCS_DIR="$REPO_ROOT/services/foundry-iq-docs/docs"

CONTAINER_NAME="foundry-iq-docs"
DATASOURCE_NAME="foundry-iq-docs-ds"
INDEXER_NAME="foundry-iq-docs-indexer"

SEARCH_API_VERSION="2024-07-01"
KNOWLEDGE_API_VERSION="2026-04-01"

# ─── Resolve configuration from the azd environment ────────────────────
env_value() {
  azd env get-value "$1" 2>/dev/null || echo ""
}

RESOURCE_GROUP="${AZURE_RESOURCE_GROUP:-$(env_value AZURE_RESOURCE_GROUP)}"
STORAGE_ACCOUNT="${STORAGE_ACCOUNT:-$(env_value storageAccountName)}"
SEARCH_SERVICE="${SEARCH_SERVICE:-$(env_value searchServiceName)}"
INDEX_NAME="${AZURE_SEARCH_INDEX:-$(env_value AZURE_SEARCH_INDEX)}"
INDEX_NAME="${INDEX_NAME:-foundry-iq-docs-index}"
KB_NAME="${KB_NAME:-$(env_value KB_NAME)}"
KB_NAME="${KB_NAME:-fibey-field-ops-kb}"
KS_NAME="${KS_NAME:-fibey-field-ops-ks}"

if [ -z "$RESOURCE_GROUP" ] || [ -z "$STORAGE_ACCOUNT" ] || [ -z "$SEARCH_SERVICE" ]; then
  echo "Could not resolve resource group, storage account, or search service from the azd environment."
  echo "Run 'azd provision' first."
  exit 1
fi

SEARCH_ENDPOINT="https://${SEARCH_SERVICE}.search.windows.net"
MCP_ENDPOINT="${SEARCH_ENDPOINT}/knowledgebases/${KB_NAME}/mcp"

# Retry helper for transient search-service errors (503s while warming up)
retry() {
  local attempts=6 delay=20 i
  for ((i = 1; i <= attempts; i++)); do
    if "$@"; then
      return 0
    fi
    if [ "$i" -lt "$attempts" ]; then
      echo "  Attempt ${i}/${attempts} failed — retrying in ${delay}s..."
      sleep "$delay"
    fi
  done
  return 1
}

STORAGE_RESOURCE_ID=$(az storage account show \
  --name "$STORAGE_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --query id -o tsv)

SEARCH_ADMIN_KEY="${AZURE_SEARCH_ADMIN_KEY:-}"
if [ -z "$SEARCH_ADMIN_KEY" ]; then
  SEARCH_ADMIN_KEY=$(az search admin-key show \
    --service-name "$SEARCH_SERVICE" \
    --resource-group "$RESOURCE_GROUP" \
    --query primaryKey -o tsv)
fi

echo ""
echo "Resource Group  : $RESOURCE_GROUP"
echo "Storage Account : $STORAGE_ACCOUNT"
echo "Search Service  : $SEARCH_SERVICE"
echo "Search Endpoint : $SEARCH_ENDPOINT"
echo "Index           : $INDEX_NAME"
echo "Knowledge Base  : $KB_NAME"
echo ""

# ─── 1. Upload documents ───────────────────────────────────────────────
echo "=== Uploading documents to blob storage ==="
az storage blob upload-batch \
  --source "$DOCS_DIR" \
  --destination "$CONTAINER_NAME" \
  --account-name "$STORAGE_ACCOUNT" \
  --auth-mode login \
  --overwrite \
  --no-progress \
  --only-show-errors >/dev/null
echo "✓ Uploaded $(find "$DOCS_DIR" -maxdepth 1 -type f | wc -l | tr -d ' ') documents"

# ─── 2. Create data source ─────────────────────────────────────────────
echo ""
echo "=== Creating search data source ==="
retry curl --fail-with-body -sS -o /dev/null -X PUT "${SEARCH_ENDPOINT}/datasources/${DATASOURCE_NAME}?api-version=${SEARCH_API_VERSION}" \
  -H "Content-Type: application/json" \
  -H "api-key: ${SEARCH_ADMIN_KEY}" \
  -d "{
    \"name\": \"${DATASOURCE_NAME}\",
    \"type\": \"azureblob\",
    \"credentials\": {
      \"connectionString\": \"ResourceId=${STORAGE_RESOURCE_ID};\"
    },
    \"container\": {
      \"name\": \"${CONTAINER_NAME}\"
    }
  }"
echo "✓ Data source created"

# ─── 3. Create search index ────────────────────────────────────────────
echo ""
echo "=== Creating search index ==="
retry curl --fail-with-body -sS -o /dev/null -X PUT "${SEARCH_ENDPOINT}/indexes/${INDEX_NAME}?api-version=${SEARCH_API_VERSION}" \
  -H "Content-Type: application/json" \
  -H "api-key: ${SEARCH_ADMIN_KEY}" \
  -d "{
    \"name\": \"${INDEX_NAME}\",
    \"fields\": [
      {\"name\": \"id\", \"type\": \"Edm.String\", \"key\": true, \"filterable\": true, \"retrievable\": true},
      {\"name\": \"content\", \"type\": \"Edm.String\", \"searchable\": true, \"retrievable\": true},
      {\"name\": \"metadata_storage_path\", \"type\": \"Edm.String\", \"filterable\": true, \"retrievable\": true},
      {\"name\": \"metadata_storage_name\", \"type\": \"Edm.String\", \"filterable\": true, \"retrievable\": true}
    ],
    \"semantic\": {
      \"configurations\": [
        {
          \"name\": \"default\",
          \"prioritizedFields\": {
            \"prioritizedContentFields\": [{\"fieldName\": \"content\"}],
            \"titleField\": {\"fieldName\": \"metadata_storage_name\"}
          }
        }
      ],
      \"defaultConfiguration\": \"default\"
    }
  }"
echo "✓ Index created"

# ─── 4. Create indexer ─────────────────────────────────────────────────
echo ""
echo "=== Creating search indexer ==="
retry curl --fail-with-body -sS -o /dev/null -X PUT "${SEARCH_ENDPOINT}/indexers/${INDEXER_NAME}?api-version=${SEARCH_API_VERSION}" \
  -H "Content-Type: application/json" \
  -H "api-key: ${SEARCH_ADMIN_KEY}" \
  -d "{
    \"name\": \"${INDEXER_NAME}\",
    \"dataSourceName\": \"${DATASOURCE_NAME}\",
    \"targetIndexName\": \"${INDEX_NAME}\",
    \"fieldMappings\": [
      {
        \"sourceFieldName\": \"metadata_storage_path\",
        \"targetFieldName\": \"id\",
        \"mappingFunction\": {
          \"name\": \"base64Encode\"
        }
      }
    ],
    \"parameters\": {
      \"configuration\": {
        \"parsingMode\": \"default\",
        \"dataToExtract\": \"contentAndMetadata\"
      }
    },
    \"schedule\": null
  }"
echo "✓ Indexer created"

# ─── 5. Run indexer ────────────────────────────────────────────────────
echo ""
echo "=== Running indexer ==="
curl -sS -o /dev/null -X POST "${SEARCH_ENDPOINT}/indexers/${INDEXER_NAME}/run?api-version=${SEARCH_API_VERSION}" \
  -H "api-key: ${SEARCH_ADMIN_KEY}" \
  -H "Content-Length: 0"
echo "✓ Indexer triggered — documents will be indexed shortly"

# ─── 6. Check status ───────────────────────────────────────────────────
echo ""
echo "=== Checking indexer status ==="
sleep 5
STATUS=$(curl --fail-with-body -sS "${SEARCH_ENDPOINT}/indexers/${INDEXER_NAME}/status?api-version=${SEARCH_API_VERSION}" \
  -H "api-key: ${SEARCH_ADMIN_KEY}")
echo "$STATUS" | python3 -c "
import json, sys
d = json.load(sys.stdin)
hist = d.get('lastResult') or {}
print(f\"Status: {hist.get('status', 'unknown')}\")
print(f\"Items processed: {hist.get('itemsProcessed', 0)}\")
print(f\"Items failed: {hist.get('itemsFailed', 0)}\")
"

# ─── 7. Create knowledge source ────────────────────────────────────────
echo ""
echo "=== Creating knowledge source ==="
retry curl --fail-with-body -sS -o /dev/null -X PUT "${SEARCH_ENDPOINT}/knowledgesources/${KS_NAME}?api-version=${KNOWLEDGE_API_VERSION}" \
  -H "Content-Type: application/json" \
  -H "api-key: ${SEARCH_ADMIN_KEY}" \
  -d "{
    \"name\": \"${KS_NAME}\",
    \"kind\": \"searchIndex\",
    \"description\": \"Knowledge source for Fibey Field Ops FoundryIQ documents.\",
    \"encryptionKey\": null,
    \"searchIndexParameters\": {
      \"searchIndexName\": \"${INDEX_NAME}\",
      \"semanticConfigurationName\": \"default\",
      \"sourceDataFields\": [
        { \"name\": \"metadata_storage_name\" },
        { \"name\": \"metadata_storage_path\" }
      ],
      \"searchFields\": [
        { \"name\": \"content\" }
      ]
    }
  }"
echo "✓ Knowledge source created"

# ─── 8. Create knowledge base ──────────────────────────────────────────
echo ""
echo "=== Creating knowledge base ==="
retry curl --fail-with-body -sS -o /dev/null -X PUT "${SEARCH_ENDPOINT}/knowledgebases/${KB_NAME}?api-version=${KNOWLEDGE_API_VERSION}" \
  -H "Content-Type: application/json" \
  -H "api-key: ${SEARCH_ADMIN_KEY}" \
  -d "{
    \"name\": \"${KB_NAME}\",
    \"description\": \"Knowledge base for Fibey Field Ops procedures, safety guidance, and troubleshooting docs.\",
    \"knowledgeSources\": [
      { \"name\": \"${KS_NAME}\" }
    ],
    \"encryptionKey\": null
  }"
echo "✓ Knowledge base created"

echo ""
echo "=== Knowledge base ready ==="
echo "Search endpoint : ${SEARCH_ENDPOINT}"
echo "Index name      : ${INDEX_NAME}"
echo "Knowledge source: ${KS_NAME}"
echo "Knowledge base  : ${KB_NAME}"
echo "MCP endpoint    : ${MCP_ENDPOINT}"
