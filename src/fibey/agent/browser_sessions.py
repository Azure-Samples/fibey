"""Field-briefing eligibility, browser argument validation, and session cleanup."""

import json
import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import httpx
from agent_framework import (
    AgentContext, AgentMiddleware, AgentSession, Content,
    FunctionInvocationContext, FunctionMiddleware, FunctionTool,
)
from agent_framework.exceptions import ToolExecutionException
from mcp.shared.exceptions import McpError
from pydantic import BaseModel

from fibey.tool_results import is_browser_tool, tool_result_is_error

logger = logging.getLogger(__name__)

_CREATE = "browser_automation___create_browser_session"
_CLOSE = "browser_automation___close_browser_session"
_READS = {"browser_automation___browser_navigate", "browser_automation___browser_snapshot"}
_TURN = "fibey_browser_turn"
_NETWORK_ID = re.compile(
    r"\bOLT-[A-Z0-9]+(?:-[A-Z0-9]+)*\b"
    r"|\b(?:NE|NW|SE|SW|Central|Harbor)-\d+\b"
    r"|\bsegment\s+[A-Z][A-Z0-9_-]*\d[A-Z0-9_-]*\b",
    re.IGNORECASE,
)


def browser_session_middleware(
    dispatch: Callable[..., Awaitable[Any]],
) -> list[AgentMiddleware | FunctionMiddleware]:
    guard = BrowserSessionGuard(dispatch)
    return [BrowserTurnGuard(guard), guard]


def _has_network_identifier(value: Any) -> bool:
    if isinstance(value, Content):
        return value.type == "text" and _has_network_identifier(value.text)
    if isinstance(value, str):
        return _NETWORK_ID.search(value) is not None
    if isinstance(value, list):
        return any(_has_network_identifier(item) for item in value)
    if isinstance(value, Mapping):
        return any(_has_network_identifier(item) for item in value.values())
    return False


class BrowserTurnGuard(AgentMiddleware):
    def __init__(self, guard: "BrowserSessionGuard") -> None:
        self._guard = guard

    async def process(
        self, context: AgentContext, call_next: Callable[[], Awaitable[None]],
    ) -> None:
        session = context.session
        if session is None:
            # Hosted Responses runs omit the session; initialize it before SkillsProvider does.
            session = context.session = AgentSession()
        message = next((
            message for message in reversed(context.messages) if message.role == "user"
        ), None)
        session.state[_TURN] = {
            "field_briefing": False,
            "network_identified": _has_network_identifier(message.contents) if message else False,
            "work_order_checked": False,
        }

        async def cleanup() -> None:
            await self._guard.close_remaining(session)

        if context.stream:
            context.stream_cleanup_hooks.append(cleanup)
        try:
            await call_next()
        finally:
            if not context.stream:
                await cleanup()


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
            arguments = arguments.get("arguments", {})
        if not isinstance(name, str) or not is_browser_tool(name):
            if context.session is not None and name == "load_skill":
                turn = context.session.state.get(_TURN)
                if turn is not None and arguments.get("skill_name", "").lower() == "field-briefing":
                    turn["field_briefing"] = True
            await call_next()
            if (
                context.session is not None
                and isinstance(name, str)
                and name.startswith("work_orders___get_work_order_")
                and not tool_result_is_error(context.result)
            ):
                turn = context.session.state.get(_TURN)
                if turn is not None:
                    turn["work_order_checked"] = True
                    if _has_network_identifier(context.result):
                        turn["network_identified"] = True
            if context.session is not None and isinstance(name, str) and (
                name.startswith("work_orders___get_work_order_")
                or "knowledge_base" in name
            ) and not tool_result_is_error(context.result):
                turn = context.session.state.get(_TURN)
                if (
                    turn and turn["field_briefing"] and turn["work_order_checked"]
                    and not turn["network_identified"]
                ):
                    context.result = [
                        *FunctionTool.parse_result(context.result),
                        Content.from_text(
                            "Runtime briefing guidance: No segment or OLT was identified in "
                            "the current request or fetched work order. Do not call Browser "
                            "Automation or discover browser tools for this briefing. Network "
                            "identifiers in reference documents do not identify this job's "
                            "network elements. Continue the work order, parts, procedures, "
                            "and safety briefing, and state that network status was not "
                            "checked because no segment or OLT was identified. Do not cite "
                            "the Status Dashboard or append its source line; it was not read."
                        ),
                    ]
            return
        if context.session is None:
            raise ToolExecutionException("Browser operations require an agent session")

        turn = context.session.state.get(_TURN)
        if name != _CLOSE and turn and turn["field_briefing"] and not turn["network_identified"]:
            logger.warning("Rejected Status Dashboard browsing for a briefing without a network identifier")
            context.result = json.dumps({
                "isError": True,
                "error": (
                    "Network status was not checked because no segment or OLT was identified "
                    "in the current request or fetched work order. Do not browse or cite the "
                    "Status Dashboard. Continue with the available briefing information."
                ),
            })
            return

        if name not in {_CREATE, _CLOSE, *_READS}:
            logger.warning("Rejected an unknown or unsupported Browser Automation operation")
            context.result = json.dumps({
                "isError": True,
                "error": (
                    "Unknown or unsupported Browser Automation operation. "
                    "Use only the exact discovered create_browser_session, browser_navigate, "
                    "browser_snapshot, and close_browser_session names."
                ),
            })
            return

        required = [] if name == _CREATE else ["browserSessionId"]
        if name == "browser_automation___browser_navigate":
            required.append("url")
        if not isinstance(arguments, Mapping):
            logger.warning("Rejected a browser operation with non-object arguments")
            context.result = json.dumps({
                "isError": True, "error": "Invalid browser arguments; arguments must be an object.",
            })
            return
        if any(
            not isinstance(arguments.get(key), str) or not arguments[key].strip()
            for key in required
        ):
            logger.warning("Rejected a browser operation with missing required arguments")
            context.result = json.dumps({
                "isError": True,
                "error": (
                    f"Invalid browser arguments; required: {', '.join(required)}. "
                    "Wait for create_browser_session to return its browserSessionId, "
                    "then retry once with the complete schema arguments. No read was attempted."
                ),
            })
            return

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
        cleanup = await self._cleanup(session_id, session)
        logger.warning("Status Dashboard read failed; browser cleanup %s", cleanup)
        context.result = json.dumps({
            "isError": True,
            "error": "I couldn't read the Status Dashboard.",
            "cleanup": f"Session cleanup {cleanup}. Do not retry the read or close the session again.",
        })

    async def _cleanup(self, session_id: str, session: dict[str, bool]) -> str:
        if session["cleanup_attempted"]:
            return "already attempted"
        # Mark before awaiting so concurrent or model-requested closes cannot retry cleanup.
        session["cleanup_attempted"] = True
        try:
            result = await self._dispatch(
                "call_tool", name=_CLOSE, arguments={"browserSessionId": session_id},
            )
            return "failed" if tool_result_is_error(result) else "completed"
        except (ToolExecutionException, McpError, httpx.HTTPError, TimeoutError):
            return "failed"

    async def close_remaining(self, agent_session: AgentSession) -> None:
        for session_id, session in agent_session.state.get("fibey_browser_sessions", {}).items():
            if not session["cleanup_attempted"]:
                cleanup = await self._cleanup(session_id, session)
                if cleanup == "failed":
                    logger.warning("Status Dashboard turn-end browser cleanup failed")
                else:
                    logger.info("Status Dashboard turn-end browser cleanup %s", cleanup)
