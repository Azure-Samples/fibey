import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

from agent_framework import ChatResponse, ChatResponseUpdate, Content, ResponseStream
from fastapi.testclient import TestClient

from fibey.gateway import api_server


class GatewayModeTests(unittest.TestCase):
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
