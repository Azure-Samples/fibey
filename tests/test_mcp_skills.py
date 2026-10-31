import json
import unittest

from agent_framework import Agent, AgentSession, SessionContext, SkillsProvider
from agent_framework.foundry import FoundryChatClient
from azure.identity import AzureCliCredential
from mcp.types import ListResourcesResult, ReadResourceResult, Resource, TextResourceContents
from mcp.shared.exceptions import McpError

from fibey.agent.mcp_skills import McpSkillsSource


SKILL_URI = "skill://field-briefing/SKILL.md"
SKILL_BODY = "---\nname: field-briefing\n---\n\nFetch the work order and check parts."
SKILL_DESCRIPTION = "Give technicians a complete briefing before heading to a job."


class FakeMcpSession:
    def __init__(self):
        self.pages = {
            None: ListResourcesResult(
                resources=[Resource(name="skills-index", uri="skill://index.json")],
                nextCursor="skills",
            ),
            "skills": ListResourcesResult(resources=[
                Resource(
                    name="field-briefing",
                    uri=SKILL_URI,
                    description="Generic entry document description.",
                ),
            ]),
        }
        self.bodies = {
            "skill://index.json": json.dumps({"skills": [{
                "name": "field-briefing",
                "description": SKILL_DESCRIPTION,
                "type": "skill-md",
                "url": SKILL_URI,
            }]}),
            SKILL_URI: SKILL_BODY,
        }
        self.read_results = {}
        self.list_error = None
        self.read_errors = {}

    async def list_resources(self, *, params=None):
        if self.list_error is not None:
            raise self.list_error
        return self.pages[params.cursor if params else None]

    async def read_resource(self, uri):
        if str(uri) in self.read_errors:
            raise self.read_errors[str(uri)]
        if str(uri) in self.read_results:
            return self.read_results[str(uri)]
        return ReadResourceResult(contents=[
            TextResourceContents(uri=uri, text=self.bodies[str(uri)]),
        ])


class McpSkillsTests(unittest.IsolatedAsyncioTestCase):
    async def context_for(self, session):
        provider = SkillsProvider(McpSkillsSource(session))
        context = SessionContext(input_messages=[])
        agent = Agent(client=FoundryChatClient(
            project_endpoint="https://example.services.ai.azure.com/api/projects/test",
            model="test-model",
            credential=AzureCliCredential(),
        ))
        await provider.before_run(
            agent=agent, session=AgentSession(), context=context, state={},
        )
        return context

    async def test_toolbox_skill_is_advertised_and_loadable(self):
        context = await self.context_for(FakeMcpSession())
        self.assertIn(SKILL_DESCRIPTION, "\n".join(context.instructions))
        load_skill, = context.tools
        self.assertEqual(load_skill.name, "load_skill")
        result = await load_skill.invoke(skill_name="field-briefing")
        self.assertIn(
            "Fetch the work order and check parts.",
            "\n".join(content.text or "" for content in result),
        )

    async def test_empty_skill_body_fails_instead_of_advertising_a_broken_skill(self):
        for contents in ([], [TextResourceContents(uri=SKILL_URI, text=" \n ")]):
            with self.subTest(contents=contents):
                session = FakeMcpSession()
                session.read_results[SKILL_URI] = ReadResourceResult(contents=contents)
                with self.assertRaisesRegex(ValueError, "Empty.*field-briefing"):
                    await self.context_for(session)

    async def test_zero_attached_skills_warns_and_adds_no_skill_tools(self):
        for has_index in (False, True):
            with self.subTest(has_index=has_index):
                session = FakeMcpSession()
                session.pages = {None: ListResourcesResult(resources=(
                    [Resource(name="skills-index", uri="skill://index.json")]
                    if has_index else []
                ))}
                session.bodies["skill://index.json"] = '{"skills":[]}'
                with self.assertLogs("fibey.agent.mcp_skills", level="WARNING") as logs:
                    context = await self.context_for(session)
                self.assertIn("No skills attached", "\n".join(logs.output))
                self.assertEqual(context.tools, [])
                self.assertEqual(context.instructions, [])

    async def test_mcp_errors_are_propagated_without_disk_fallback(self):
        from mcp.types import ErrorData

        for operation in ("list", "index", "skill"):
            with self.subTest(operation=operation):
                session = FakeMcpSession()
                error = McpError(ErrorData(code=-32603, message="Toolbox unavailable"))
                if operation == "list":
                    session.list_error = error
                else:
                    uri = "skill://index.json" if operation == "index" else SKILL_URI
                    session.read_errors[uri] = error
                with self.assertRaisesRegex(McpError, "Toolbox unavailable"):
                    await self.context_for(session)

    async def test_missing_or_malformed_index_is_an_error_not_zero_skills(self):
        for missing in (False, True):
            with self.subTest(missing=missing):
                session = FakeMcpSession()
                if missing:
                    session.pages[None] = session.pages["skills"]
                else:
                    session.bodies["skill://index.json"] = "not json"
                with self.assertRaises(ValueError):
                    await self.context_for(session)


if __name__ == "__main__":
    unittest.main()
