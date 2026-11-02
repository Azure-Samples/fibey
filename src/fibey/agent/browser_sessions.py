"""Session-scoped cleanup for failed Status Dashboard reads."""

import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import httpx
from agent_framework import Content, FunctionInvocationContext, FunctionMiddleware
from agent_framework.exceptions import ToolExecutionException
from mcp.shared.exceptions import McpError
from pydantic import BaseModel

from fibey.tool_results import tool_result_is_error

logger = logging.getLogger(__name__)

_CREATE = "browser_automation___create_browser_session"
_CLOSE = "browser_automation___close_browser_session"
_READS = {"browser_automation___browser_navigate", "browser_automation___browser_snapshot"}


def _created_session_id(result: Any) -> str | None:
    if isinstance(result, Content):
        return _created_session_id(result.text) if result.type == "text" else None
    if isinstance(result, str):
        try:
            return _created_session_id(json.loads(result))
        except json.JSONDecodeError:
            return None
    if isinstance(result, list):
        return next((session_id for item in result if (session_id := _created_session_id(item))), None)
    if isinstance(result, Mapping):
        session_id = result.get("browserSessionId")
        if isinstance(session_id, str) and session_id:
            return session_id
        return _created_session_id(result.get("content")) or _created_session_id(result.get("text"))
    return None


class BrowserSessionGuard(FunctionMiddleware):
    def __init__(self, dispatch: Callable[..., Awaitable[Any]]) -> None:
        self._dispatch = dispatch

    async def process(
        self, context: FunctionInvocationContext, call_next: Callable[[], Awaitable[None]],
    ) -> None:
        arguments = context.arguments
        if isinstance(arguments, BaseModel):
            arguments = arguments.model_dump()
        name = context.function.name
        if name == "call_tool":
            name = arguments.get("name")
            arguments = arguments.get("arguments") or {}
        if name not in {_CREATE, _CLOSE, *_READS}:
            await call_next()
            return
        if context.session is None:
            raise ToolExecutionException("Browser operations require an agent session")

        sessions: dict[str, dict[str, bool]] = context.session.state.setdefault("fibey_browser_sessions", {})
        if name == _CREATE:
            await call_next()
            if not tool_result_is_error(context.result):
                session_id = _created_session_id(context.result)
                if session_id is None:
                    raise ToolExecutionException("Browser session creation returned no browserSessionId")
                sessions[session_id] = {"cleanup_attempted": False, "failed": False}
            return

        session_id = arguments.get("browserSessionId")
        session = sessions.get(session_id) if isinstance(session_id, str) else None
        if session is None or session["cleanup_attempted"] or session["failed"]:
            logger.warning("Rejected a browser operation without an active owned session")
            context.result = json.dumps({
                "isError": True,
                "error": "No active browser session. A failed read or cleanup must not be retried.",
            })
            return

        if name == _CLOSE:
            session["cleanup_attempted"] = True
            await call_next()
            return

        try:
            await call_next()
        except ToolExecutionException:
            failed = True
        else:
            failed = tool_result_is_error(context.result)
        if not failed:
            return

        session["failed"] = True
        # Mark before awaiting so a concurrent or model-requested close cannot retry cleanup.
        if not session["cleanup_attempted"]:
            session["cleanup_attempted"] = True
            try:
                result = await self._dispatch(
                    "call_tool", name=_CLOSE, arguments={"browserSessionId": session_id},
                )
                cleanup = "failed" if tool_result_is_error(result) else "completed"
            except (ToolExecutionException, McpError, httpx.HTTPError, TimeoutError):
                cleanup = "failed"
        else:
            cleanup = "already attempted"
        logger.warning("Status Dashboard read failed; browser cleanup %s", cleanup)
        context.result = json.dumps({
            "isError": True,
            "error": "I couldn't read the Status Dashboard.",
            "cleanup": f"Session cleanup {cleanup}. Do not retry the read or close the session again.",
        })
