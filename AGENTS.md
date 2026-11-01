## Agent skills

### Runtime and deployment

The deployed flow is React UI -> FastAPI gateway -> Foundry hosted agent ->
Foundry Toolbox. `azure.yaml` deploys `fibey-agent` with `host: azure.ai.agent`
and five Container Apps: UI, gateway, inventory MCP, work orders API, and
status dashboard. Keep gateway and hosted-agent concerns separate.

`AGENT_MODE` accepts only `local` (in-process development) and `hosted`
(deployed demo). Hosted gateway requests use the Foundry Responses API and
translate events to the existing SSE contract; preserve tool names,
`load_skill` activity, citations, errors, and session continuity.

### Toolbox workflow skills

`skills/<name>/SKILL.md` is the git source of truth. Runtime skills come from
Foundry Toolbox MCP Resources (SEP-2640), discovered via `resources/list` and
read via `resources/read` using `skill://index.json`.

Python Agent Framework 1.5.0 has no native MCP-skills source.
`src/fibey/agent/mcp_skills.py` implements `McpSkillsSource` for
`SkillsProvider` in both agent modes; keep the `load_skill` tool name.
Configured Toolbox/resource failures must propagate; zero attached skills
warn and add no skill tools.

Both modes use `MCPStreamableHTTPTool` with an explicit authenticated HTTP
client and `Foundry-Features: Toolboxes=V1Preview,Skills=V1Preview`.
The hosted source caches skills until restart.

### Publishing and preview caveats

The `azure.yaml` postprovision hook sets up the knowledge base; the postdeploy
hook runs `scripts/setup-toolbox.sh`. The publisher uses REST, not `azd ai`,
and checks skill content even when the Toolbox exists. Changed content creates
a skill version and updates its default; changed attachment names create an
immutable Toolbox version repeating existing tools. References are unpinned
and resolve each skill's `default_version`.

Run `./scripts/setup-toolbox.sh` for skill changes, or set
`FORCE_TOOLBOX_UPDATE=1` to rebuild tool definitions; redeploy `fibey-agent`
after publication to refresh its cache. The publisher registers inventory,
work orders, and KB MCP tools, not browser automation for the status dashboard.

Toolboxes/skills and the hosted Responses/KB APIs use preview contracts.
The existing-environment deployment and hosted UI flow were verified; a fresh
resource-group deployment was not exercised. See `docs/deployment.md` and
`docs/toolbox-integration.md` before changing deployment or skill integration.

### Issue tracker

Issues are tracked in this repository's GitHub Issues. See `docs/agents/issue-tracker.md`.

### Triage labels

Triage uses the canonical labels `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, and `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Domain documentation uses a single-context layout. See `docs/agents/domain.md`.
