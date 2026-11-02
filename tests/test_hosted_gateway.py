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
    def chat_events(self, upstream_events, message="Brief me on WO-007."):
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
                "message": message, "session_id": "gateway-test",
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

    def test_rate_limits_are_actionable_without_sdk_details_or_links(self):
        events = self.chat_events([{
            "type": "response.failed",
            "error": {
                "code": "rate_limit_exceeded",
                "message": "openai.RateLimitError: token quota exceeded; https://example.invalid/quota",
            },
        }])
        self.assertIn(("error", {
            "message": "The model's rate limit was reached. Wait a minute before trying again.",
        }), events)
        self.assertNotIn("https://", json.dumps(events))
        self.assertEqual(events[-1], ("done", "[DONE]"))

    def test_status_question_names_the_site_without_forwarding_browser_session_data(self):
        live_view = "https://browser.example/live?token=private-session"
        tool = "browser_automation___create_browser_session"
        call = {
            "type": "function_call", "id": "fc-browser", "call_id": "call-browser",
            "name": "call_tool", "arguments": json.dumps({
                "name": tool,
                "arguments": {"liveViewUrl": live_view, "browserSessionId": "private-session"},
            }),
        }
        answer = "NE-3 has an outage. [Status Dashboard](https://status.example/)"
        events = self.chat_events([
            output_event("added", {**call, "arguments": ""}),
            output_event("done", call),
            output_event("done", {
                "type": "function_call_output", "call_id": "call-browser",
                "output": json.dumps({"liveViewUrl": live_view}),
            }),
            {"type": "response.output_text.delta", "delta": answer},
            {"type": "response.failed", "error": "Model unavailable"},
        ], message="Any alerts on NE-3?")
        activities = [data for kind, data in events if kind == "activity"]
        self.assertEqual([data["status"] for data in activities], ["running", "complete"])
        for data in activities:
            self.assertEqual(data["tool"], tool)
            self.assertEqual(data["call_id"], "call-browser")
            self.assertEqual(data["detail"], "Status Dashboard")
            self.assertEqual(json.loads(data["args"]), {"site": "Status Dashboard"})
            self.assertEqual(data["result"], "")
        self.assertNotIn("private-session", json.dumps(events))
        self.assertIn(("delta", {"content": answer}), events)
        self.assertIn(("error", {"message": "Agent error: Model unavailable"}), events)
        self.assertEqual(events[-1], ("done", "[DONE]"))

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

    def test_browser_mcp_lifecycle_events_keep_the_site_and_hide_session_links(self):
        tool = "browser_automation___browser_navigate"
        live_view = "https://browser.example/live?token=private-session"
        call = {
            "type": "mcp_call", "id": "mcp-browser", "name": tool,
            "arguments": '{"url":"https://status.example/","browserSessionId":"private-session"}',
        }
        endings = [
            (output_event("done", {**call, "output": live_view}), "complete"),
            (output_event("done", {**call, "error": live_view}), "error"),
            (output_event("done", {
                **call, "output": json.dumps({"isError": True, "error": live_view}),
            }), "error"),
            *(
                ({"type": kind, "item_id": "mcp-browser", field: live_view}, status)
                for kind, field, status in (
                    ("response.mcp_call.completed", "output", "complete"),
                    ("response.mcp_call_completed", "result", "complete"),
                    ("response.mcp_call.failed", "error", "error"),
                    ("response.mcp_call_failed", "error", "error"),
                )
            ),
            *(
                ({"type": kind, "item_id": "mcp-browser", "output": json.dumps({
                    "content": [{"type": "text", "text": json.dumps({"isError": True, "error": live_view})}],
                })}, "error")
                for kind in ("response.mcp_call.completed", "response.mcp_call_completed")
            ),
        ]
        for ending, status in endings:
            with self.subTest(kind=ending["type"], status=status):
                events = self.chat_events([
                    output_event("added", call), ending,
                ], message="Is NE-3 healthy?")
                activities = [data for kind, data in events if kind == "activity"]
                self.assertEqual([data["status"] for data in activities], ["running", status])
                for data in activities:
                    self.assertEqual(data["tool"], tool)
                    self.assertEqual(json.loads(data["args"]), {"site": "Status Dashboard"})
                    self.assertEqual(data["result"], "")
                self.assertNotIn("private-session", json.dumps(events))
                if status == "error":
                    self.assertEqual(activities[-1]["detail"], "Could not read Status Dashboard")

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
            json.dumps({"content": [{
                "type": "text", "text": '{"isError":true,"error":"Search unavailable"}',
            }]}),
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
