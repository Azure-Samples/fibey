"""Agent Framework skills discovered through the Foundry Toolbox MCP session."""

import logging
from typing import Protocol

from agent_framework import InlineSkill, Skill, SkillFrontmatter, SkillsSource
from mcp.types import (
    ListResourcesResult,
    PaginatedRequestParams,
    ReadResourceResult,
    TextResourceContents,
)
from pydantic import AnyUrl, BaseModel

logger = logging.getLogger(__name__)


class McpSkillsSession(Protocol):
    async def list_resources(
        self, *, params: PaginatedRequestParams | None,
    ) -> ListResourcesResult: ...

    async def read_resource(self, uri: AnyUrl) -> ReadResourceResult: ...


class _SkillReference(BaseModel):
    name: str
    description: str
    url: AnyUrl


class _SkillIndex(BaseModel):
    skills: list[_SkillReference]


class McpSkillsSource(SkillsSource):
    def __init__(self, session: McpSkillsSession) -> None:
        self._session = session
        self._skills: list[Skill] | None = None

    async def _read_text(self, uri: AnyUrl) -> str:
        result = await self._session.read_resource(uri)
        text = "\n".join(
            content.text for content in result.contents
            if isinstance(content, TextResourceContents)
        )
        if not text.strip():
            raise ValueError(f"Empty toolbox skill resource: {uri}")
        return text

    async def get_skills(self) -> list[Skill]:
        if self._skills is not None:
            return self._skills
        resources = []
        cursor = None
        while True:
            page = await self._session.list_resources(
                params=PaginatedRequestParams(cursor=cursor) if cursor else None,
            )
            resources.extend(page.resources)
            cursor = page.nextCursor
            if cursor is None:
                break

        index_resource = next((
            resource for resource in resources if str(resource.uri) == "skill://index.json"
        ), None)
        if index_resource is None:
            if any(str(resource.uri).startswith("skill://") for resource in resources):
                raise ValueError("Toolbox skills index is missing")
            logger.warning("No skills attached to the Foundry Toolbox")
            self._skills = []
            return self._skills
        index = _SkillIndex.model_validate_json(await self._read_text(index_resource.uri))
        skills: list[Skill] = []
        for reference in index.skills:
            skills.append(InlineSkill(
                frontmatter=SkillFrontmatter(
                    name=reference.name, description=reference.description,
                ),
                instructions=await self._read_text(reference.url),
            ))
            logger.info("Loaded toolbox skill: %s", reference.name)
        if not skills:
            logger.warning("No skills attached to the Foundry Toolbox")
        self._skills = skills
        return skills
