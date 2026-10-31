import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent_framework import Content

from fibey.agent.agent import run_agent


class ToolActivityTests(unittest.IsolatedAsyncioTestCase):
    async def activities_for(self, result, *, exception=None):
        async def updates():
            yield SimpleNamespace(contents=[
                Content.from_function_call(
                    call_id="kb-call",
                    name="call_tool",
                    arguments=json.dumps({
                        "name": "knowledge_base___knowledge_base_retrieve",
                        "arguments": {"queries": ["What are the splicing safety protocols?"]},
                    }),
                ),
            ])
            yield SimpleNamespace(contents=[
                Content.from_function_result(
                    call_id="kb-call", result=result, exception=exception,
                ),
            ])

        agent = SimpleNamespace(run=lambda *args, **kwargs: updates())
        with patch("fibey.agent.agent.create_agent", return_value=(agent, [])):
            return [
                event async for event in run_agent("Splicing safety", {})
                if event["type"] == "activity"
            ]

    async def test_framework_failure_is_an_error_not_a_completion(self):
        events = await self.activities_for("Error: Function failed.")
        self.assertEqual(events[-1]["status"], "error")
        self.assertNotIn("complete", [event["status"] for event in events])
        self.assertEqual(events[-1]["call_id"], "kb-call")
        self.assertEqual(events[-1]["tool"], "knowledge_base___knowledge_base_retrieve")
        self.assertIn("Function failed", events[-1]["result"])

    async def test_structured_mcp_failure_is_an_error(self):
        events = await self.activities_for({
            "isError": True,
            "content": [{"type": "text", "text": "Retrieval unavailable"}],
        })
        self.assertEqual(events[-1]["status"], "error")
        self.assertIn("Retrieval unavailable", events[-1]["result"])

    async def test_json_error_payload_is_an_error(self):
        events = await self.activities_for('{"error": "Search is not configured"}')
        self.assertEqual(events[-1]["status"], "error")

    async def test_exception_metadata_is_an_error(self):
        events = await self.activities_for(None, exception="Retrieval timed out")
        self.assertEqual(events[-1]["status"], "error")
        self.assertIn("Retrieval timed out", events[-1]["result"])

    async def test_successful_error_guidance_is_not_a_failure(self):
        events = await self.activities_for({
            "isError": False,
            "content": [{"type": "text", "text": "Troubleshooting splice errors"}],
        })
        self.assertEqual(events[-1]["status"], "complete")
        self.assertNotIn("error", [event["status"] for event in events])


if __name__ == "__main__":
    unittest.main()
