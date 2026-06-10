"""Fibey Agent — Foundry hosted agent entrypoint.

Deployed to Azure AI Foundry as a hosted agent using the responses protocol.
Follows the foundry-samples agent-framework pattern.

Architecture (hosted mode):
    Single agent with:
    - Foundry Toolbox MCP tool (work orders, inventory, knowledge base)
    - SkillsProvider for deterministic routing (field-briefing,
      inventory-lookup, knowledge-retrieval, work-order-management,
      work-order-preparation)

Environment variables (auto-injected by Foundry hosting):
    FOUNDRY_PROJECT_ENDPOINT — project endpoint URL

Environment variables (set in agent.yaml):
    AZURE_AI_MODEL_DEPLOYMENT_NAME — model deployment name (e.g. gpt-4.1-mini)
    TOOLBOX_MCP_URL — Foundry Toolbox MCP endpoint URL
"""

import logging
import os
from pathlib import Path

import httpx
import mcp.types
from agent_framework import Agent, MCPStreamableHTTPTool, SkillsProvider
from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.identity import (
    AzureDeveloperCliCredential,
    ChainedTokenCredential,
    ManagedIdentityCredential,
    get_bearer_token_provider,
)
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SYSTEM_PROMPT_PATH = Path(__file__).parent / "prompts" / "system_prompt.md"
SKILLS_DIR = Path(__file__).parent / "skills"


# ---------------------------------------------------------------------------
# Workaround: Azure AI Search KB MCP returns resource content with uri: null
# or uri: "", which fails pydantic AnyUrl validation in the MCP SDK.
# Relax the uri field to accept any string (or None) so parsing succeeds.
# ---------------------------------------------------------------------------
for _cls in [mcp.types.ResourceContents, mcp.types.TextResourceContents, mcp.types.BlobResourceContents]:
    _cls.model_fields["uri"].annotation = str | None
    _cls.model_fields["uri"].default = None
    _cls.model_fields["uri"].metadata = []
for _cls in [mcp.types.ResourceContents, mcp.types.TextResourceContents,
             mcp.types.BlobResourceContents, mcp.types.EmbeddedResource,
             mcp.types.CallToolResult]:
    _cls.model_rebuild(force=True)


class ToolboxAuth(httpx.Auth):
    """httpx Auth that injects a fresh bearer token for the Foundry Toolbox MCP endpoint."""

    def __init__(self, token_provider) -> None:
        self._token_provider = token_provider

    def auth_flow(self, request):
        """Add Authorization header with a fresh token on every request."""
        request.headers["Authorization"] = f"Bearer {self._token_provider()}"
        yield request


def _load_system_prompt() -> str:
    if SYSTEM_PROMPT_PATH.exists():
        return SYSTEM_PROMPT_PATH.read_text()
    return "You are Fibey, a helpful AI assistant for fiber optics field operations."


def main() -> None:
    """Start the hosted agent server."""
    user_mi = ManagedIdentityCredential(client_id=os.getenv("AZURE_CLIENT_ID"))
    azd_cli = AzureDeveloperCliCredential(
        tenant_id=os.getenv("AZURE_TENANT_ID"), process_timeout=60
    )
    credential = ChainedTokenCredential(user_mi, azd_cli)

    model = os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME") or os.environ.get("FOUNDRY_MODEL")
    project_endpoint = os.environ["FOUNDRY_PROJECT_ENDPOINT"]
    logger.info("Model: %s | Endpoint: %s", model, project_endpoint[:60])

    client = FoundryChatClient(
        project_endpoint=project_endpoint,
        model=model,
        credential=credential,
    )

    # --- Toolbox MCP Tool ---
    tools: list = []
    toolbox_url = os.environ.get("TOOLBOX_MCP_URL", "")
    if toolbox_url:
        logger.info("Registering Toolbox MCP: %s", toolbox_url[:80])
        token_provider = get_bearer_token_provider(
            credential, "https://ai.azure.com/.default"
        )
        toolbox_http_client = httpx.AsyncClient(
            auth=ToolboxAuth(token_provider),
            headers={"Foundry-Features": "Toolboxes=V1Preview"},
            timeout=120.0,
        )
        toolbox_mcp_tool = MCPStreamableHTTPTool(
            name="toolbox",
            url=toolbox_url,
            http_client=toolbox_http_client,
            load_prompts=False,
        )
        tools.append(toolbox_mcp_tool)
        logger.info("Toolbox MCP registered successfully")
    else:
        logger.warning("TOOLBOX_MCP_URL not set — agent will have no tools")

    # --- Skills ---
    skills_provider = None
    if SKILLS_DIR.is_dir():
        try:
            skills_provider = SkillsProvider.from_paths(
                skill_paths=str(SKILLS_DIR),
            )
            logger.info("Loaded skills from %s", SKILLS_DIR)
        except Exception as exc:
            logger.warning("Failed to load skills: %s", exc, exc_info=True)

    agent = Agent(
        client=client,
        name="fibey",
        instructions=_load_system_prompt(),
        tools=tools,
        context_providers=[skills_provider] if skills_provider else None,
        default_options={"store": False},
    )

    server = ResponsesHostServer(agent)
    server.run()


if __name__ == "__main__":
    main()
