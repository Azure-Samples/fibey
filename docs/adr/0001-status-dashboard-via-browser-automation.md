---
status: accepted
---

# Read the Status Dashboard through Browser Automation

The Status Dashboard stands in for a page owned by another team or party that has no API, so the agent reads it with the Foundry Browser Automation tool registered in the Toolbox. We deliberately did not add a status API or MCP tool, even though that would be more reliable and cheaper, because the demo's point is browsing a system we cannot change.

## Considered Options

- **Status JSON/MCP tool:** rejected. It would require owning the dashboard's data, which contradicts the scenario.
- **Private dashboard via Playwright private traffic mode:** deferred. It is preview and needs a VNet and a delegated subnet. The dashboard gets external ingress for now; it holds only fake data.
- **Web IQ:** not applicable. It grounds on the public web and is unrelated to Foundry IQ.

## Consequences

- The agent is restricted to one configured URL (`STATUS_DASHBOARD_URL`), read-only, and treats page text as data.
- If the page is unreachable or its layout changes, the agent says it could not read the dashboard. It does not guess and there is no fallback.
- Requires a Playwright Workspace in a region that supports hosted agents.
