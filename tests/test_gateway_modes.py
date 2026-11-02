import asyncio
import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

import httpx
from agent_framework import Agent, ChatResponse, ChatResponseUpdate, Content, FunctionTool, Message, ResponseStream
from agent_framework.exceptions import ToolExecutionException
from agent_framework.foundry import FoundryChatClient
from azure.identity import AzureCliCredential
from fastapi.testclient import TestClient

from fibey.gateway import api_server
from fibey.agent.browser_sessions import BrowserSessionGuard


class GatewayModeTests(unittest.TestCase):
    def test_failed_browser_reads_close_once_even_when_the_model_retries_or_cleanup_fails(self):
        create = "browser_automation___create_browser_session"
        navigate = "browser_automation___browser_navigate"
        close = "browser_automation___close_browser_session"
        browser_args = {"browserSessionId": "private-session", "url": "https://status.example/"}
        steps = [
            (create, {}),
            (navigate, browser_args),
            (navigate, browser_args),
            (close, {"browserSessionId": "private-session"}),
            None,
        ]

        class ScriptedClient(FoundryChatClient):
            def __init__(self):
                super().__init__(
                    project_endpoint="https://foundry.example/api/projects/fibey",
                    model="controlled-model", credential=AzureCliCredential(),
                )
                self.steps = iter(steps * 2)
                self.response_index = 0

            def _inner_get_response(self, *, stream, **kwargs):
                step = next(self.steps)
                self.response_index += 1
                contents = [Content.from_function_call(
                    call_id=f"call-{self.response_index}", name="call_tool",
                    arguments=json.dumps({"name": step[0], "arguments": step[1]}),
                )] if step else [Content.from_text("I couldn't read the Status Dashboard.")]
                response = ChatResponse(messages=[Message(role="assistant", contents=contents)])

                async def updates():
                    yield ChatResponseUpdate(role="assistant", contents=contents)

                async def result():
                    return response

                return ResponseStream(updates(), finalizer=lambda _: response) if stream else result()

        for cleanup_fails in (False, True):
            with self.subTest(cleanup_fails=cleanup_fails):
                calls = []

                def call_tool(name: str, arguments: dict) -> str:
                    calls.append(name)
                    if name == create:
                        return json.dumps({"browserSessionId": "private-session"})
                    if name == navigate:
                        if cleanup_fails:
                            raise ToolExecutionException("Read timed out")
                        return json.dumps({"content": [{
                            "type": "text", "text": '{"isError":true,"error":"Read failed"}',
                        }]})
                    if cleanup_fails:
                        raise ToolExecutionException("Session expired")
                    return '{"closed":true}'

                async def dispatch(tool_name, **kwargs):
                    self.assertEqual(tool_name, "call_tool")
                    return call_tool(**kwargs)

                model = ScriptedClient()
                self.addCleanup(lambda model=model: asyncio.run(model.client.close()))
                self.addCleanup(lambda model=model: asyncio.run(model.project_client.close()))
                agent = Agent(
                    client=model, tools=[FunctionTool(name="call_tool", func=call_tool)],
                    middleware=[BrowserSessionGuard(dispatch)],
                    default_options={"store": False},
                )
                with (
                    patch.object(api_server, "AGENT_MODE", "local"),
                    patch("fibey.agent.agent.create_agent", return_value=(agent, [])),
                    TestClient(api_server.app) as client,
                ):
                    # The same middleware must keep distinct chat sessions isolated.
                    for session_id in ("cleanup-first", "cleanup-second"):
                        response = client.post("/api/chat", json={
                            "message": "Any NE-3 alerts?", "session_id": session_id,
                        })
                        client.post("/api/sessions/reset", json={"session_id": session_id})
                        self.assertNotIn("event: error", response.text)
                        self.assertNotIn("private-session", response.text)
                        self.assertIn("I couldn't read the Status Dashboard.", response.text)
                self.assertEqual(calls, [create, navigate, close] * 2)

    def test_discovery_exposes_consistent_dispatch_despite_partial_native_tools(self):
        def toolbox(request):
            if request.method != "POST":
                return httpx.Response(405)
            body = json.loads(request.content)
            if "id" not in body:
                return httpx.Response(202)
            method = body["method"]
            if method == "initialize":
                result = {
                    "protocolVersion": body["params"]["protocolVersion"],
                    "capabilities": {"tools": {}, "resources": {}},
                    "serverInfo": {"name": "controlled-toolbox", "version": "1"},
                }
            elif method == "tools/list":
                result = {"tools": [
                    {"name": name, "inputSchema": schema}
                    for name, schema in (
                        ("tool_search", {"type": "object", "properties": {"query": {"type": "string"}}}),
                        ("call_tool", {"type": "object", "properties": {
                            "name": {"type": "string"}, "arguments": {"type": "object"},
                        }, "required": ["name"]}),
                        ("browser_automation___create_browser_session", {"type": "object", "properties": {}}),
                        ("browser_automation___close_browser_session", {"type": "object", "properties": {
                            "browserSessionId": {"type": "string"},
                        }}),
                    )
                ]}
            elif method == "resources/list":
                result = {"resources": []}
            elif method == "ping":
                result = {}
            else:
                self.fail(f"Unexpected MCP method: {method}")
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

        def model_response(*args, **kwargs):
            functions = [tool.name for tool in kwargs["options"]["tools"]]
            self.assertEqual(sorted(functions), ["call_tool", "tool_search"])

            async def updates():
                yield ChatResponseUpdate(contents=[Content.from_text("Tool discovery ready.")])
            return ResponseStream(updates(), finalizer=lambda _: ChatResponse())

        async def send(client, request, **kwargs):
            response = toolbox(request)
            response.request = request
            return response

        with (
            patch.dict(os.environ, {
                "FOUNDRY_PROJECT_ENDPOINT": "https://foundry.example/api/projects/fibey",
                "TOOLBOX_MCP_URL": "https://toolbox.example/mcp",
                "TOOLBOX_API_KEY": "",
            }),
            patch.object(api_server, "AGENT_MODE", "local"),
            patch("azure.identity.AzureCliCredential.get_token"),
            patch.object(httpx.AsyncClient, "send", new=send),
            patch("agent_framework.foundry.FoundryChatClient.get_response", side_effect=model_response),
            TestClient(api_server.app) as client,
        ):
            response = client.post("/api/chat", json={
                "message": "Any NE-3 alerts?", "session_id": "dispatch-regression",
            })
            client.post("/api/sessions/reset", json={"session_id": "dispatch-regression"})

        self.assertNotIn("event: error", response.text)
        self.assertIn("Tool discovery ready.", response.text)

    def test_local_status_activity_names_only_the_site_even_when_browser_calls_fail(self):
        tool = "browser_automation___browser_snapshot"
        live_view = "https://browser.example/live?token=private-session"
        answer = "NE-3 has an outage. [Status Dashboard](https://status.example/)"
        for failed in (False, True):
            with self.subTest(failed=failed):
                args = json.dumps({
                    "name": tool,
                    "arguments": {"browserSessionId": "private-session", "liveViewUrl": live_view},
                })

                async def updates():
                    # The wrapped name arrives only after the first argument chunk.
                    for chunk in (args[:8], args[8:]):
                        yield ChatResponseUpdate(contents=[Content.from_function_call(
                            call_id="browser-call", name="call_tool", arguments=chunk,
                        )])
                    yield ChatResponseUpdate(contents=[Content.from_function_result(
                        call_id="browser-call",
                        result={"isError": failed, "liveViewUrl": live_view},
                    )])
                    yield ChatResponseUpdate(contents=[Content.from_text(answer)])

                with (
                    patch.dict(os.environ, {
                        "FOUNDRY_PROJECT_ENDPOINT": "https://foundry.example/api/projects/fibey",
                        "TOOLBOX_MCP_URL": "",
                    }),
                    patch.object(api_server, "AGENT_MODE", "local"),
                    patch("azure.identity.AzureCliCredential.get_token"),
                    patch("agent_framework.Agent.run", side_effect=lambda *args, **kwargs: updates()),
                    self.assertLogs("fibey.agent.agent", level="INFO") as logs,
                    TestClient(api_server.app) as client,
                ):
                    response = client.post("/api/chat", json={
                        "message": "Any alerts on NE-3?", "session_id": "local-browser-test",
                    })
                    client.post("/api/sessions/reset", json={"session_id": "local-browser-test"})

                self.assertEqual(response.status_code, 200)
                activities = [
                    json.loads(block.split("\ndata: ")[1])
                    for block in response.text.strip().split("\n\n")
                    if block.startswith("event: activity\n")
                ]
                self.assertEqual(
                    [data["status"] for data in activities],
                    ["running", "error" if failed else "complete"],
                )
                for data in activities:
                    self.assertEqual(data["tool"], tool)
                    self.assertEqual(data["call_id"], "browser-call")
                    self.assertEqual(json.loads(data["args"]), {"site": "Status Dashboard"})
                    self.assertEqual(data["result"], "")
                self.assertEqual(
                    activities[-1]["detail"],
                    "Could not read Status Dashboard" if failed else "Status Dashboard",
                )
                self.assertNotIn("private-session", response.text)
                self.assertNotIn("private-session", "\n".join(logs.output))
                self.assertIn(f'data: {json.dumps({"content": answer})}\n\n', response.text)
                self.assertTrue(response.text.endswith("event: done\ndata: [DONE]\n\n"))

    def test_gateway_refuses_removed_or_unknown_modes_at_startup(self):
        for mode in ("containerapp", "unknown"):
            with self.subTest(mode=mode):
                result = subprocess.run(
                    [sys.executable, "-c", "import fibey.gateway.api_server"],
                    env={**os.environ, "AGENT_MODE": mode},
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("AGENT_MODE must be 'local' or 'hosted'", result.stderr)

    def test_gateway_starts_in_each_supported_mode(self):
        for mode in ("local", "hosted"):
            with self.subTest(mode=mode):
                result = subprocess.run(
                    [sys.executable, "-c", (
                        "from fastapi.testclient import TestClient; "
                        "from fibey.gateway.api_server import app; "
                        "client = TestClient(app); "
                        "print(client.get('/api/health').json()['mode'])"
                    )],
                    env={**os.environ, "AGENT_MODE": mode},
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), mode)

    def test_local_chat_streams_model_text_and_completes(self):
        async def updates():
            yield ChatResponseUpdate(
                role="assistant",
                contents=[Content.from_text("WO-007 briefing")],
            )

        with (
            patch.dict(os.environ, {
                "FOUNDRY_PROJECT_ENDPOINT": "https://foundry.example/api/projects/fibey",
                "TOOLBOX_MCP_URL": "",
            }),
            patch.object(api_server, "AGENT_MODE", "local"),
            patch("azure.identity.AzureCliCredential.get_token"),
            patch("agent_framework.foundry.FoundryChatClient.get_response",
                  return_value=ResponseStream(updates(), finalizer=lambda _: ChatResponse())),
            TestClient(api_server.app) as client,
        ):
            response = client.post("/api/chat", json={
                "message": "Brief me on WO-007.", "session_id": "local-gateway-test",
            })
            client.post("/api/sessions/reset", json={"session_id": "local-gateway-test"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["x-session-id"], "local-gateway-test")
        self.assertEqual(response.text, (
            'event: delta\ndata: {"content": "WO-007 briefing"}\n\n'
            'event: done\ndata: [DONE]\n\n'
        ))
