import os
import subprocess
import sys
import unittest
from unittest.mock import patch

from agent_framework import ChatResponse, ChatResponseUpdate, Content, ResponseStream
from fastapi.testclient import TestClient

from fibey.gateway import api_server


class GatewayModeTests(unittest.TestCase):
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
