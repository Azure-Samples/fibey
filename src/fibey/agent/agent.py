"""
Single agent definition with Foundry Toolbox MCP connection.

The agent calls the Toolbox as one MCP endpoint; the Toolbox dispatches
to individual tools (FoundryIQ, Work Orders OpenAPI, Inventory MCP) behind
the scenes.  When the Toolbox is configured, FoundryIQ provides knowledge
base retrieval; otherwise a local Azure AI Search function tool is used
as a fallback.
"""

import asyncio
import os
import json
import logging
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any, AsyncGenerator

import httpx
from azure.identity import AzureCliCredential, DefaultAzureCredential
from agent_framework import (
    Agent,
    AgentResponseUpdate,
    AgentSession,
    FunctionTool,
    MCPStreamableHTTPTool,
    ResponseStream,
    SkillsProvider,
)
from agent_framework.foundry import FoundryChatClient

from fibey.agent.mcp_skills import McpSkillsSource
from fibey.tool_results import tool_result_is_error, tool_search_results

logger = logging.getLogger(__name__)

SYSTEM_PROMPT_PATH = Path(__file__).parent / "prompts" / "system_prompt.md"
_TOKEN_SCOPE = "https://ai.azure.com/.default"

# Azure AI Search configuration for direct KB queries
_SEARCH_ENDPOINT = os.getenv("AZURE_SEARCH_ENDPOINT", "")
_SEARCH_INDEX = os.getenv("AZURE_SEARCH_INDEX", "foundry-iq-docs-index")
_SEARCH_API_KEY = os.getenv("AZURE_SEARCH_API_KEY", "")


def _load_system_prompt() -> str:
    """Load the system prompt from markdown file."""
    if SYSTEM_PROMPT_PATH.exists():
        return SYSTEM_PROMPT_PATH.read_text()
    return "You are Fibey, a helpful AI assistant."


def _get_credential():
    """Get Azure credential, preferring CLI for local dev."""
    try:
        cred = AzureCliCredential()
        cred.get_token(_TOKEN_SCOPE)
        return cred
    except Exception:
        return DefaultAzureCredential()


def _get_token_sync(credential) -> str:
    return credential.get_token(_TOKEN_SCOPE).token


class _ToolboxAuth(httpx.Auth):
    """httpx Auth that injects a fresh bearer token for Toolbox MCP."""
    
    def __init__(self, credential):
        self._credential = credential
    
    def auth_flow(self, request):
        """Add Authorization header with a fresh token on every request."""
        request.headers["Authorization"] = f"Bearer {self._credential.get_token(_TOKEN_SCOPE).token}"
        yield request


class _ToolboxApiKeyAuth(httpx.Auth):
    """httpx Auth that injects the Cognitive Services account key for Toolbox MCP."""

    def __init__(self, api_key: str):
        self._api_key = api_key

    def auth_flow(self, request):
        request.headers["api-key"] = self._api_key
        yield request


def _create_toolbox_mcp(credential) -> MCPStreamableHTTPTool | None:
    """Create the Toolbox MCP tool if endpoint is configured."""
    toolbox_url = os.getenv("TOOLBOX_MCP_URL", "")
    if not toolbox_url:
        logger.warning("TOOLBOX_MCP_URL not set — running without Toolbox")
        return None

    if "api-version" not in toolbox_url:
        separator = "&" if "?" in toolbox_url else "?"
        toolbox_url = f"{toolbox_url}{separator}api-version=v1"
    
    logger.info("Toolbox MCP URL: %s", toolbox_url)

    api_key = os.getenv("TOOLBOX_API_KEY", "")
    if api_key:
        logger.info("Toolbox auth: api-key (TOOLBOX_API_KEY)")
        auth = _ToolboxApiKeyAuth(api_key)
    else:
        logger.info("Toolbox auth: Entra bearer token")
        auth = _ToolboxAuth(credential)

    auth_http_client = httpx.AsyncClient(
        auth=auth,
        headers={"Foundry-Features": "Toolboxes=V1Preview,Skills=V1Preview"},
        timeout=120.0,
    )

    return MCPStreamableHTTPTool(
        name="toolbox",
        url=toolbox_url,
        http_client=auth_http_client,
        load_prompts=False,
    )


async def knowledge_base_search(query: str, top: int = 5) -> str:
    """Search the fiber optics field operations knowledge base.

    Searches across procedures, safety protocols, troubleshooting guides,
    equipment specs, cable types, installation standards, OTDR testing,
    and network architecture documentation.

    Args:
        query: The search query describing what you need to find.
        top: Maximum number of results to return (default 5).

    Returns:
        JSON string with search results including document name and content.
    """
    if not _SEARCH_API_KEY:
        return json.dumps({"error": "AZURE_SEARCH_API_KEY not configured"})

    search_url = f"{_SEARCH_ENDPOINT}/indexes/{_SEARCH_INDEX}/docs/search?api-version=2024-07-01"
    payload = {
        "search": query,
        "queryType": "semantic",
        "semanticConfiguration": "default",
        "top": top,
        "select": "content,metadata_storage_name",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            search_url,
            json=payload,
            headers={"api-key": _SEARCH_API_KEY, "Content-Type": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()

    results = []
    for doc in data.get("value", []):
        results.append({
            "source": doc.get("metadata_storage_name", "unknown"),
            "content": doc.get("content", ""),
        })

    return json.dumps({"results": results, "count": len(results)})


def _create_kb_search_tool() -> FunctionTool | None:
    """Create the knowledge base search tool if search is configured."""
    if not _SEARCH_API_KEY:
        logger.warning("AZURE_SEARCH_API_KEY not set — running without KB search")
        return None

    return FunctionTool(
        name="knowledge_base",
        description=(
            "Search the fiber optics field operations knowledge base. "
            "Covers: splicing procedures, safety protocols, OTDR testing, "
            "cable types, equipment specs, installation standards, "
            "network architecture, and troubleshooting guides."
        ),
        func=knowledge_base_search,
    )


def create_agent() -> tuple[Agent, list]:
    """Create the agent with Foundry client and Toolbox MCP connection."""
    credential = _get_credential()

    client = FoundryChatClient(
        credential=credential,
    )

    tools = []
    toolbox_mcp = _create_toolbox_mcp(credential)
    if toolbox_mcp:
        tools.append(toolbox_mcp)

    # Only add the local KB tool when Toolbox is NOT configured
    # (Toolbox provides knowledge_base via azure_ai_search / FoundryIQ)
    if not toolbox_mcp:
        kb_tool = _create_kb_search_tool()
        if kb_tool:
            tools.append(kb_tool)

    agent = Agent(
        client=client,
        name="fibey",
        instructions=_load_system_prompt(),
        tools=tools,
    )

    return agent, tools


async def run_agent(message: str, session: dict) -> AsyncGenerator[dict, None]:
    """
    Run the agent and yield streaming events.

    Events yielded:
    - {"type": "delta", "content": "..."}
    - {"type": "activity", "tool": "...", "status": "...", "detail": "..."}
    - {"type": "citation", "source": "...", "url": "..."}
    """
    agent, tools = create_agent()

    agent_session = session.get("agent_session")
    if not agent_session:
        agent_session = AgentSession()
        session["agent_session"] = agent_session

    async with AsyncExitStack() as stack:
        # Initialize MCP tools
        for tool in tools:
            if isinstance(tool, MCPStreamableHTTPTool):
                await stack.enter_async_context(tool)
                if tool.session is None:
                    raise RuntimeError("Toolbox MCP session was not initialized")
                agent.context_providers.append(SkillsProvider(McpSkillsSource(tool.session)))

        stream = agent.run(
            message,
            stream=True,
            session=agent_session,
        )

        # Track tool calls to deduplicate streaming repeats and map results back
        seen_skill_loads: set[str] = set()        # dedupe repeated load_skill for same skill
        args_key_owner: dict[str, str] = {}       # name+args key → first call_id; later identical calls are suppressed
        call_id_to_name: dict[str, str] = {}
        pending_args: dict[str, str] = {}
        suppressed_call_ids: set[str] = set()     # call_ids whose events should be hidden
        emitted_running: set[str] = set()         # call_ids that already got a running event
        emitted_complete: set[str] = set()        # call_ids that already got a complete event

        async for update in stream:
            update: AgentResponseUpdate

            if update.contents:
                for content in update.contents:
                    ctype = content.type

                    if ctype == "text":
                        yield {"type": "delta", "content": content.text or ""}

                    elif ctype in ("mcp_server_tool_call", "function_call"):
                        tool_name = getattr(content, "tool_name", None) or getattr(content, "name", None) or "tool"
                        call_id = getattr(content, "call_id", None) or tool_name
                        call_id_to_name[call_id] = tool_name
                        # Accumulate arguments across streaming chunks
                        raw_args = getattr(content, "arguments", None) or ""
                        if isinstance(raw_args, dict):
                            raw_args = json.dumps(raw_args)
                        if call_id not in pending_args:
                            pending_args[call_id] = raw_args
                            # Unwrap Foundry Toolbox tool-search-mode `call_tool` so
                            # the activity surfaces the *real* underlying tool rather
                            # than the meta-tool wrapper.
                            unwrapped_via_tool_search = False
                            if tool_name == "call_tool":
                                try:
                                    parsed = json.loads(raw_args) if raw_args else {}
                                except Exception:
                                    parsed = {}
                                inner_name = parsed.get("name") if isinstance(parsed, dict) else None
                                if inner_name:
                                    tool_name = inner_name
                                    call_id_to_name[call_id] = inner_name
                                    inner_args = parsed.get("arguments", {}) or {}
                                    if isinstance(inner_args, (dict, list)):
                                        inner_args_str = json.dumps(inner_args)
                                    else:
                                        inner_args_str = str(inner_args)
                                    pending_args[call_id] = inner_args_str
                                    raw_args = inner_args_str
                                    unwrapped_via_tool_search = True
                            # Detect duplicates early when args arrive in a single chunk.
                            # `args_key_owner` stores the FIRST call_id to register a given
                            # name+args key; subsequent calls with the same key are
                            # suppressed so the activity sidebar doesn't show duplicates.
                            skip = False
                            if tool_name == "load_skill":
                                try:
                                    parsed = json.loads(raw_args) if raw_args else {}
                                    skill_key = parsed.get("skill_name", "")
                                except Exception:
                                    skill_key = ""
                                if skill_key and skill_key in seen_skill_loads:
                                    skip = True
                                    suppressed_call_ids.add(call_id)
                                elif skill_key:
                                    seen_skill_loads.add(skill_key)
                            elif raw_args:
                                try:
                                    json.loads(raw_args)  # only dedup if args are complete JSON
                                    tool_args_key = f"{tool_name}::{raw_args}"
                                    owner = args_key_owner.get(tool_args_key)
                                    if owner is None:
                                        args_key_owner[tool_args_key] = call_id
                                    elif owner != call_id:
                                        skip = True
                                        suppressed_call_ids.add(call_id)
                                except (ValueError, TypeError):
                                    pass  # incomplete args, can't dedup yet
                            if not skip:
                                # If this is still the meta wrapper `call_tool` (because
                                # args weren't complete enough to unwrap), don't emit a
                                # placeholder yet — the safety-net unwrap at result time
                                # will emit the real tool's running event.
                                if tool_name == "call_tool" and not unwrapped_via_tool_search:
                                    pass
                                else:
                                    early_event = {
                                        "type": "activity",
                                        "tool": tool_name,
                                        "call_id": call_id,
                                        "status": "running",
                                        "detail": f"Calling {tool_name}...",
                                    }
                                    yield early_event
                                    emitted_running.add(call_id)
                        else:
                            pending_args[call_id] += raw_args

                    elif ctype in ("mcp_server_tool_result", "function_result"):
                        call_id = getattr(content, "call_id", None) or ""
                        tool_name = call_id_to_name.get(call_id) or getattr(content, "tool_name", None) or getattr(content, "name", None) or "tool"

                        result_obj = getattr(content, "result", None)
                        exception = getattr(content, "exception", None)
                        is_error = bool(exception) or tool_result_is_error(result_obj)
                        result_repr = (
                            json.dumps(result_obj)
                            if isinstance(result_obj, (dict, list))
                            else str(result_obj) if result_obj is not None else ""
                        )
                        error_detail = str(exception) if exception else result_repr
                        logger.info(
                            "TOOL_RESULT call_id=%s tool=%s is_error=%s preview=%s",
                            call_id, tool_name, is_error, error_detail.replace("\n", " ")[:400],
                        )

                        # Safety net: if early-chunk unwrap of `call_tool` failed
                        # (args were chunked), retry now that pending_args is complete.
                        extra_meta: dict[str, Any] = {}
                        if tool_name == "call_tool":
                            try:
                                parsed_wrapper = json.loads(pending_args.get(call_id, "") or "{}")
                            except Exception:
                                parsed_wrapper = {}
                            inner_name = parsed_wrapper.get("name") if isinstance(parsed_wrapper, dict) else None
                            if inner_name:
                                tool_name = inner_name
                                call_id_to_name[call_id] = inner_name
                                inner_args = parsed_wrapper.get("arguments", {}) or {}
                                inner_args_str = (
                                    json.dumps(inner_args)
                                    if isinstance(inner_args, (dict, list))
                                    else str(inner_args)
                                )
                                pending_args[call_id] = inner_args_str

                        # For tool_search, parse the returned tool list and attach
                        # it to the complete event so the UI can render which tools
                        # were discovered (and later mark which ones were used).
                        if tool_name == "tool_search":
                            try:
                                extra_meta["results"] = tool_search_results(result_obj)
                            except (ValueError, TypeError, AttributeError) as parse_exc:
                                logger.debug("tool_search result parse failed: %s", parse_exc)

                        # If this call was suppressed at early-chunk time
                        # (e.g., duplicate name+args), skip emitting running/complete too.
                        if call_id in suppressed_call_ids:
                            emitted_running.add(call_id)
                            emitted_complete.add(call_id)
                            continue

                        args_str = pending_args.get(call_id, "")

                        # Late dedup: if args were chunked, we couldn't detect duplicates
                        # at early-emit time. Re-check ownership with the full args now.
                        if tool_name == "load_skill":
                            try:
                                parsed = json.loads(args_str) if args_str else {}
                                skill_name = parsed.get("skill_name", "")
                            except Exception:
                                skill_name = ""
                            if skill_name and skill_name in seen_skill_loads and call_id not in emitted_running:
                                # The skill was loaded before this call_id got an early emit.
                                suppressed_call_ids.add(call_id)
                                emitted_running.add(call_id)
                                emitted_complete.add(call_id)
                                continue
                            if skill_name and skill_name not in seen_skill_loads:
                                seen_skill_loads.add(skill_name)
                            detail = f"Loading skill: {skill_name}" if skill_name else f"Calling {tool_name}..."
                        else:
                            tool_args_key = f"{tool_name}::{args_str}"
                            owner = args_key_owner.get(tool_args_key)
                            if owner is None:
                                args_key_owner[tool_args_key] = call_id
                            elif owner != call_id and call_id not in emitted_running:
                                # An identical call already won the race; suppress this one.
                                suppressed_call_ids.add(call_id)
                                emitted_running.add(call_id)
                                emitted_complete.add(call_id)
                                continue

                            detail = f"Calling {tool_name}..."
                            try:
                                parsed = json.loads(args_str) if args_str else {}
                                if isinstance(parsed, dict):
                                    for key in ("work_order_id", "part_id", "query"):
                                        val = parsed.get(key)
                                        if val:
                                            detail = f"Calling {tool_name} ({key}={val})"
                                            break
                            except Exception:
                                pass

                        # Emit "running" with full args if we haven't already.
                        if call_id not in emitted_running:
                            running_event = {
                                "type": "activity",
                                "tool": tool_name,
                                "call_id": call_id,
                                "status": "running",
                                "detail": detail,
                                "args": args_str,
                            }
                            running_event.update(extra_meta)
                            yield running_event
                            emitted_running.add(call_id)
                        else:
                            # We already emitted a "running" event (from the early chunk).
                            # Re-emit a running with the full args so the UI's args display
                            # gets updated to the complete value before complete fires.
                            update_event = {
                                "type": "activity",
                                "tool": tool_name,
                                "call_id": call_id,
                                "status": "running",
                                "detail": detail,
                                "args": args_str,
                            }
                            update_event.update(extra_meta)
                            yield update_event

                        # Report the tool outcome, not just that execution finished.
                        if call_id not in emitted_complete:
                            complete_event = {
                                "type": "activity",
                                "tool": tool_name,
                                "call_id": call_id,
                                "status": "error" if is_error else "complete",
                                "detail": (
                                    f"Failed {tool_name}: {error_detail[:200]}"
                                    if is_error else f"Completed {tool_name}"
                                ),
                                "args": args_str,
                            }
                            if is_error:
                                complete_event["result"] = error_detail[:2000]
                            complete_event.update(extra_meta)
                            yield complete_event
                            emitted_complete.add(call_id)

                    else:
                        # Log unknown content types for debugging
                        import logging
                        logging.getLogger(__name__).debug(
                            "Unknown content type: %s attrs=%s",
                            ctype,
                            {k: str(v)[:100] for k, v in vars(content).items()} if hasattr(content, '__dict__') else str(content)[:200]
                        )
