import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from fibey.gateway import api_server


def output_event(kind, item):
    return {"type": f"response.output_item.{kind}", "item": item}


class HostedGatewayTests(unittest.TestCase):
    def chat_events(self, upstream_events):
        stream = "".join(
            f"data: {json.dumps(event)}\n\n" for event in upstream_events
        )
        async_client = httpx.AsyncClient
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=stream)
        )
        with (
            patch.object(api_server, "AGENT_MODE", "hosted"),
            patch.object(api_server, "HOSTED_AGENT_ENDPOINT", "https://foundry.example/api/projects/fibey"),
            patch("azure.identity.ChainedTokenCredential") as credential,
            patch.object(httpx, "AsyncClient", side_effect=lambda **kwargs: async_client(
                transport=transport, **kwargs,
            )),
            TestClient(api_server.app) as client,
        ):
            credential.return_value.get_token.return_value = SimpleNamespace(token="test-token")
            response = client.post("/api/chat", json={
                "message": "Brief me on WO-007.", "session_id": "gateway-test",
            })
        self.assertEqual(response.status_code, 200)
        events = []
        for block in response.text.strip().split("\n\n"):
            kind, data = block.split("\n")
            raw = data.removeprefix("data: ")
            events.append((
                kind.removeprefix("event: "),
                raw if raw == "[DONE]" else json.loads(raw),
            ))
        return events

    def test_loaded_skill_keeps_its_name_and_arguments_in_the_activity_sidebar(self):
        call = {
            "type": "function_call", "id": "fc-skill", "call_id": "call-skill",
            "name": "load_skill", "arguments": "",
        }
        args = '{"skill_name":"field-briefing"}'
        events = self.chat_events([
            output_event("added", call),
            {"type": "response.function_call_arguments.done", "item_id": "fc-skill",
             "arguments": args},
            output_event("done", {**call, "arguments": args}),
            output_event("done", {
                "type": "function_call_output", "call_id": "call-skill",
                "output": "Toolbox field-briefing instructions",
            }),
            {"type": "response.output_text.delta", "delta": "WO-007 briefing"},
            {"type": "response.completed", "response": {"id": "response-skill"}},
        ])
        completed = [
            data for kind, data in events
            if kind == "activity" and data["status"] == "complete"
        ]
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0]["tool"], "load_skill")
        self.assertEqual(json.loads(completed[0]["args"]), {"skill_name": "field-briefing"})
        self.assertIn(("delta", {"content": "WO-007 briefing"}), events)
        self.assertEqual(events[-1], ("done", "[DONE]"))

    def test_discovery_and_wrapped_calls_expose_operational_tools(self):
        tool = "inventory___check_stock_batch"
        search = {
            "type": "function_call", "id": "fc-search", "call_id": "call-search",
            "name": "tool_search", "arguments": '{"query":"stock","limit":10}',
        }
        wrapped = {
            "type": "function_call", "id": "fc-stock", "call_id": "call-stock",
            "name": "call_tool", "arguments": json.dumps({
                "name": tool, "arguments": {"part_ids": ["FIB-004"]},
            }),
        }
        events = self.chat_events([
            output_event("added", search),
            output_event("done", search),
            output_event("done", {
                "type": "function_call_output", "call_id": "call-search",
                "output": json.dumps({"tools": [
                    {"name": tool, "description": "Check stock levels\nMore details"},
                ]}),
            }),
            output_event("added", {**wrapped, "arguments": ""}),
            output_event("done", wrapped),
            output_event("done", {
                "type": "function_call_output", "call_id": "call-stock",
                "output": '{"results":[{"id":"FIB-004","stock_quantity":12}]}',
            }),
        ])
        activities = [data for kind, data in events if kind == "activity"]
        search_result = next(data for data in activities if data["status"] == "complete")
        self.assertEqual(search_result["results"], [
            {"name": tool, "description": "Check stock levels"},
        ])
        stock = [data for data in activities if data["call_id"] == "call-stock"]
        self.assertTrue(stock)
        self.assertTrue(all(data["tool"] == tool for data in stock))
        self.assertEqual(json.loads(stock[-1]["args"]), {"part_ids": ["FIB-004"]})

    def test_failed_tool_outputs_are_not_reported_as_successful(self):
        for output in (
            "Error: Function failed.",
            '{"isError":true,"content":[{"type":"text","text":"Search unavailable"}]}',
        ):
            with self.subTest(output=output):
                call = {
                    "type": "function_call", "id": "fc-kb", "call_id": "call-kb",
                    "name": "knowledge_base___knowledge_base_retrieve",
                    "arguments": '{"queries":["Splicing safety"]}',
                }
                events = self.chat_events([
                    output_event("added", call),
                    output_event("done", call),
                    output_event("done", {
                        "type": "function_call_output", "call_id": "call-kb",
                        "output": output,
                    }),
                ])
                activities = [data for kind, data in events if kind == "activity"]
                self.assertEqual(activities[-1]["status"], "error")
                self.assertEqual(activities[-1]["result"], output)
                self.assertNotIn("complete", [data["status"] for data in activities])
