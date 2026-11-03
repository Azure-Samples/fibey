"""Replay model dispatch mistakes through the middleware used in both agent modes."""

import asyncio
import json
import unittest
from pathlib import Path

from agent_framework import (
    Agent, AgentSession, ChatResponse, ChatResponseUpdate, Content, FunctionTool,
    InlineSkill, Message, ResponseStream, SkillFrontmatter, SkillsProvider,
)
from agent_framework.foundry import FoundryChatClient
from azure.identity import AzureCliCredential

from fibey.agent.browser_sessions import browser_session_middleware


CREATE = "browser_automation___create_browser_session"
NAVIGATE = "browser_automation___browser_navigate"
SNAPSHOT = "browser_automation___browser_snapshot"
CLOSE = "browser_automation___close_browser_session"
WORK_ORDER = "work_orders___get_work_order_work_orders__work_order_id__get"
SESSION = "controlled-browser-session"
URL = "https://status.example/"
BRIEFING = (
    "Give me a full field briefing for WO-007 \u2014 parts availability, "
    "relevant procedures, and safety guidelines."
)


def dispatch(name, arguments=None):
    return ("call_tool", {"name": name, "arguments": arguments or {}})


def load_briefing():
    return ("load_skill", {"skill_name": "field-briefing"})


class ScriptedClient(FoundryChatClient):
    def __init__(self):
        super().__init__(
            project_endpoint="https://foundry.example/api/projects/fibey",
            model="controlled-model", credential=AzureCliCredential(),
        )
        self.steps = iter(())
        self.results = []
        self.call_index = 0

    def _inner_get_response(self, *, stream, messages, **kwargs):
        for message in messages:
            for content in message.contents:
                if content.type == "function_result":
                    self.results.append(content.result)
        step = next(self.steps, [])
        if isinstance(step, Exception):
            raise step
        contents = []
        for name, arguments in step:
            self.call_index += 1
            contents.append(Content.from_function_call(
                call_id=f"call-{self.call_index}", name=name,
                arguments=json.dumps(arguments),
            ))
        if not contents:
            contents = [Content.from_text("Briefing complete.")]
        response = ChatResponse(messages=[Message(role="assistant", contents=contents)])

        async def updates():
            yield ChatResponseUpdate(role="assistant", contents=contents)

        async def result():
            return response

        return ResponseStream(updates(), finalizer=lambda _: response) if stream else result()


class BrowserSessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.model = ScriptedClient()
        self.addAsyncCleanup(self.model.client.close)
        self.addAsyncCleanup(self.model.project_client.close)
        self.calls = []
        orders = json.loads((
            Path(__file__).parents[1] / "services/work-orders-api/data/work_orders.json"
        ).read_text())
        self.work_order = next(order for order in orders if order["id"] == "WO-007")
        self.create_started = None
        self.release_create = None
        self.cleanup_fails = False
        self.knowledge_result = json.dumps({
            "guidance": "Reference architecture example: NE-3 connects to OLT-NE-201.",
        })

        async def call_tool(name: str, arguments: dict) -> str | list[Content]:
            self.calls.append((name, arguments))
            if name == WORK_ORDER:
                return json.dumps({"content": [{
                    "type": "text", "text": json.dumps(self.work_order),
                }]})
            if name == CREATE:
                if self.create_started is not None:
                    self.create_started.set()
                    await self.release_create.wait()
                return json.dumps({"browserSessionId": SESSION})
            if name == CLOSE:
                return '{"isError":true,"error":"Session expired"}' if self.cleanup_fails else '{"closed":true}'
            if "knowledge_base" in name:
                return [Content.from_text(self.knowledge_result)]
            return '{"snapshot":"NE-3 Access Ring: Outage"}'

        async def invoke(tool_name, **kwargs):
            self.assertEqual(tool_name, "call_tool")
            return await call_tool(**kwargs)

        self.agent = Agent(
            client=self.model,
            tools=[FunctionTool(name="call_tool", func=call_tool)],
            context_providers=[SkillsProvider(InlineSkill(
                frontmatter=SkillFrontmatter(name="field-briefing", description="Field briefing"),
                instructions="Prepare a briefing.",
            ))],
            middleware=browser_session_middleware(invoke),
            default_options={"store": False},
        )
        self.session = AgentSession()

    async def run_turn(self, steps, question=BRIEFING, *, stream=False, session=None):
        self.model.steps = iter(steps)
        response = self.agent.run(question, session=session or self.session, stream=stream)
        if stream:
            async for _ in response:
                pass
        else:
            await response

    async def test_original_briefing_cannot_create_a_browser_without_network_identifiers(self):
        # The original model emitted create and empty navigation in one response.
        await self.run_turn([
            [load_briefing()],
            [dispatch(WORK_ORDER, {"work_order_id": "WO-007"})],
            [dispatch(CREATE), dispatch(NAVIGATE)],
        ], stream=True)
        self.assertEqual([name for name, _ in self.calls], [WORK_ORDER])

    async def test_hosted_style_run_without_an_explicit_session_enforces_eligibility(self):
        for stream in (False, True):
            with self.subTest(stream=stream):
                self.calls.clear()
                self.model.steps = iter([
                    [load_briefing()],
                    [dispatch(WORK_ORDER, {"work_order_id": "WO-007"})],
                    [dispatch(CREATE)],
                ])
                response = self.agent.run(BRIEFING, stream=stream)
                if stream:
                    async for _ in response:
                        pass
                else:
                    await response
                self.assertEqual([name for name, _ in self.calls], [WORK_ORDER])

    async def test_hosted_style_run_closes_an_implicitly_created_session(self):
        for stream in (False, True):
            with self.subTest(stream=stream):
                self.calls.clear()
                self.model.steps = iter([
                    [dispatch(CREATE)],
                    [dispatch(NAVIGATE, {"browserSessionId": SESSION, "url": URL})],
                ])
                response = self.agent.run("Check NE-3.", stream=stream)
                if stream:
                    async for _ in response:
                        pass
                else:
                    await response
                self.assertEqual([name for name, _ in self.calls], [CREATE, NAVIGATE, CLOSE])

    async def test_briefing_results_explain_browser_ineligibility_before_model_attempts_it(self):
        await self.run_turn([
            [load_briefing()],
            [dispatch(WORK_ORDER, {"work_order_id": "WO-007"})],
            [dispatch("knowledge_base___knowledge_base_retrieve", {"queries": ["Splicing safety"]})],
        ], stream=True)
        knowledge_result = self.model.results[-1]
        self.assertIn(self.knowledge_result, str(knowledge_result))
        self.assertIn("Do not call Browser Automation", str(knowledge_result))
        self.assertIn("reference documents", str(knowledge_result))
        order_results = [
            result for result in self.model.results
            if "WO-007" in str(result)
        ]
        self.assertTrue(order_results)
        self.assertIn("Do not call Browser Automation", str(order_results[-1]))

    async def test_identified_briefing_does_not_receive_a_no_browsing_instruction(self):
        await self.run_turn([
            [load_briefing()],
            [dispatch(WORK_ORDER, {"work_order_id": "WO-007"})],
            [dispatch("knowledge_base___knowledge_base_retrieve", {"queries": ["Splicing safety"]})],
        ], BRIEFING + " Include NE-3.", stream=True)
        self.assertEqual(self.model.results[-1], self.knowledge_result)

    async def test_invented_browser_names_cannot_bypass_the_briefing_gate(self):
        await self.run_turn([
            [load_briefing()],
            [dispatch(WORK_ORDER, {"work_order_id": "WO-007"})],
            [dispatch("browser_automation___browser_create_browser_session")],
        ], stream=True)
        self.assertEqual([name for name, _ in self.calls], [WORK_ORDER])

    async def test_unknown_browser_operations_are_rejected_without_provider_dispatch(self):
        await self.run_turn([
            [dispatch(CREATE)],
            [dispatch("browser_automation___browser_click", {"browserSessionId": SESSION})],
        ], "Any NE-3 alerts?")
        self.assertEqual([name for name, _ in self.calls], [CREATE, CLOSE])

    async def test_briefing_allows_identifiers_from_current_request_or_work_order(self):
        for source in ("request", "work_order"):
            with self.subTest(source=source):
                self.calls.clear()
                question = BRIEFING
                if source == "request":
                    question += " Include NE-3 and OLT-NE-201."
                else:
                    self.work_order["description"] += " Repair segment Rural-9."
                await self.run_turn([
                    [load_briefing()],
                    [dispatch(WORK_ORDER, {"work_order_id": "WO-007"})],
                    [dispatch(CREATE)],
                    [dispatch(NAVIGATE, {"browserSessionId": SESSION, "url": URL})],
                    [dispatch(CLOSE, {"browserSessionId": SESSION})],
                ], question, session=AgentSession())
                self.assertEqual(
                    [name for name, _ in self.calls], [WORK_ORDER, CREATE, NAVIGATE, CLOSE],
                )

    async def test_previous_turn_identifiers_do_not_authorize_an_unrelated_briefing(self):
        await self.run_turn([
            [dispatch(CREATE)],
            [dispatch(NAVIGATE, {"browserSessionId": SESSION, "url": URL})],
            [dispatch(CLOSE, {"browserSessionId": SESSION})],
        ], "Any NE-3 alerts?")
        self.calls.clear()
        await self.run_turn([
            [load_briefing()],
            [dispatch(WORK_ORDER, {"work_order_id": "WO-007"})],
            [dispatch(CREATE)],
        ])
        self.assertEqual([name for name, _ in self.calls], [WORK_ORDER])

    async def test_generic_network_words_and_knowledge_results_do_not_authorize_browsing(self):
        await self.run_turn([
            [load_briefing()],
            [dispatch(
                "knowledge_base___knowledge_base_retrieve",
                {"queries": ["Procedures for OLT-NE-201 and NE-3"]},
            )],
            [dispatch(CREATE)],
        ], BRIEFING + " No segment or OLT is identified.")
        self.assertEqual(
            [name for name, _ in self.calls], ["knowledge_base___knowledge_base_retrieve"],
        )

    async def test_invalid_navigation_arguments_allow_correction_without_dispatching_a_bad_read(self):
        for arguments in (
            {}, {"browserSessionId": SESSION}, {"browserSessionId": SESSION, "url": " "},
        ):
            with self.subTest(arguments=arguments):
                self.calls.clear()
                self.model.results.clear()
                await self.run_turn([
                    [dispatch(CREATE)],
                    [dispatch(NAVIGATE, arguments)],
                    [dispatch(NAVIGATE, {"browserSessionId": SESSION, "url": URL})],
                    [dispatch(CLOSE, {"browserSessionId": SESSION})],
                ], "Check the Status Dashboard.", session=AgentSession())
                self.assertEqual([name for name, _ in self.calls], [CREATE, NAVIGATE, CLOSE])
                self.assertTrue(any("required" in str(result) for result in self.model.results))

    async def test_parallel_create_and_empty_navigation_does_not_leak_the_created_session(self):
        # Delay creation so the malformed read always executes before its result.
        self.create_started = asyncio.Event()
        self.release_create = asyncio.Event()
        for stream in (False, True):
            with self.subTest(stream=stream):
                self.calls.clear()
                self.create_started.clear()
                self.release_create.clear()
                task = asyncio.create_task(self.run_turn([
                    [dispatch(CREATE), dispatch(NAVIGATE)],
                ], "Any NE-3 alerts?", stream=stream, session=AgentSession()))
                await asyncio.wait_for(self.create_started.wait(), timeout=5)
                self.release_create.set()
                await asyncio.wait_for(task, timeout=5)
                self.assertEqual([name for name, _ in self.calls], [CREATE, CLOSE])

    async def test_sessions_are_closed_at_turn_end_even_when_the_model_omits_close(self):
        for stream in (False, True):
            with self.subTest(stream=stream):
                self.calls.clear()
                await self.run_turn([
                    [dispatch(CREATE)],
                    [dispatch(NAVIGATE, {"browserSessionId": SESSION, "url": URL})],
                    [dispatch(SNAPSHOT, {"browserSessionId": SESSION})],
                ], "Check the Status Dashboard.", stream=stream, session=AgentSession())
                self.assertEqual([name for name, _ in self.calls], [CREATE, NAVIGATE, SNAPSHOT, CLOSE])

    async def test_turn_end_cleanup_failure_is_logged_and_not_retried(self):
        self.cleanup_fails = True
        with self.assertLogs("fibey.agent.browser_sessions", level="WARNING") as logs:
            await self.run_turn([
                [dispatch(CREATE)],
            ], "Check the Status Dashboard.", stream=True)
        self.assertEqual([name for name, _ in self.calls], [CREATE, CLOSE])
        self.assertIn("turn-end browser cleanup failed", "\n".join(logs.output))
        self.calls.clear()
        await self.run_turn([], "Thanks.")
        self.assertEqual(self.calls, [])

    async def test_model_failure_after_creation_still_closes_the_session(self):
        for stream in (False, True):
            with self.subTest(stream=stream):
                self.calls.clear()
                with self.assertRaisesRegex(RuntimeError, "Controlled model failure"):
                    await self.run_turn([
                        [dispatch(CREATE)],
                        RuntimeError("Controlled model failure"),
                    ], "Any NE-3 alerts?", stream=stream, session=AgentSession())
                self.assertEqual([name for name, _ in self.calls], [CREATE, CLOSE])
