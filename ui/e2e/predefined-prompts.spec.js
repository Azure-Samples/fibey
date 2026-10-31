import { test, expect } from "@playwright/test";

const workOrderTool = "work_orders___get_work_order_work_orders__work_order_id__get";
const stockTool = "inventory___check_stock_batch";
const knowledgeTool = "knowledge_base___knowledge_base_retrieve";
const cases = [
  {
    name: "WO-003 preparation",
    prompt: "Pull up work order WO-003 and tell me what parts are needed.",
    skill: "work-order-preparation",
    tools: [workOrderTool, stockTool],
    answer: /WO-003/,
  },
  {
    name: "OTDR inventory",
    prompt: "Do we have OTDR test equipment in stock? What models are available?",
    skill: "inventory-lookup",
    tools: ["inventory___search_parts"],
    answer: /FIB-016/,
  },
  {
    name: "fusion splicing procedures",
    prompt: "What are the proper procedures for fusion splicing single-mode fiber?",
    skill: "knowledge-retrieval",
    tools: [knowledgeTool],
    answer: /splic/i,
  },
  {
    name: "WO-005 stock check",
    prompt: "Check the parts list for WO-005 and tell me if we have everything in stock.",
    skill: "work-order-preparation",
    tools: [workOrderTool, stockTool],
    answer: /WO-005/,
  },
  {
    name: "aerial installation safety",
    prompt: "What safety protocols should a technician review before performing aerial cable installation?",
    skill: "knowledge-retrieval",
    tools: [knowledgeTool],
    answer: /aerial|height|fall protection/i,
  },
  {
    name: "WO-007 field briefing",
    prompt: "Give me a full field briefing for WO-007 — parts availability, relevant procedures, and safety guidelines.",
    skill: "field-briefing",
    tools: [workOrderTool, stockTool, knowledgeTool],
    answer: /WO-007/,
  },
];

for (const scenario of cases) {
  test(`${scenario.name}: skill precedes discovery and a usable answer`, async ({ page }, testInfo) => {
    const browserErrors = [];
    page.on("pageerror", (error) => browserErrors.push(error.message));
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Fibey Field Ops" })).toBeVisible();
    const pending = page.waitForResponse(
      (response) =>
        response.url().endsWith("/api/chat") &&
        response.request().method() === "POST",
    );
    await page.getByRole("button").filter({ hasText: scenario.prompt }).click();
    const response = await pending;
    expect(response.status()).toBe(200);
    const body = await response.text();
    await testInfo.attach("chat-events", { body, contentType: "text/plain" });
    const events = body.split(/\r?\n\r?\n/).filter((block) => block.trim()).map((block) => {
      const lines = block.split(/\r?\n/);
      const event = lines.find((line) => line.startsWith("event: "))?.slice(7);
      const raw = lines.find((line) => line.startsWith("data: "))?.slice(6);
      expect(event, "Every SSE frame has an event type").toBeTruthy();
      expect(raw, "Every SSE frame has data").toBeDefined();
      return { event, data: raw === "[DONE]" ? raw : JSON.parse(raw) };
    });
    const activities = events.filter((event) => event.event === "activity").map((event) => event.data);
    const started = activities.filter((activity) => activity.status === "running");
    const completed = activities.filter((activity) => activity.status === "complete");
    const answer = events.filter((event) => event.event === "delta").map((event) => event.data.content).join("");
    console.log(`${scenario.name}: ${[...new Set(started.map((activity) => activity.tool))].join(" -> ")}`);

    expect(events.filter((event) => event.event === "error")).toEqual([]);
    expect(activities.filter((activity) => activity.status === "error")).toEqual([]);
    expect(events.filter((event) => event.event === "done")).toEqual([{ event: "done", data: "[DONE]" }]);
    expect(started[0]?.tool, "The first visible activity must load the matching skill").toBe("load_skill");
    const skillIndex = activities.findIndex((activity) => activity.tool === "load_skill" && activity.status === "complete");
    expect(skillIndex).toBeGreaterThanOrEqual(0);
    expect(JSON.parse(activities[skillIndex].args).skill_name).toBe(scenario.skill);
    const searchIndex = activities.findIndex((activity) => activity.tool === "tool_search");
    expect(searchIndex, "Discovery must wait for the skill instructions").toBeGreaterThan(skillIndex);
    const searchCompleteIndex = activities.findIndex((activity) => activity.tool === "tool_search" && activity.status === "complete");
    expect(searchCompleteIndex).toBeGreaterThanOrEqual(searchIndex);
    expect(activities[searchCompleteIndex].results.map((tool) => tool.name),
      "Discovery must return every operational capability required by the skill")
      .toEqual(expect.arrayContaining(scenario.tools));
    const operationalIndex = activities.findIndex((activity) => !["load_skill", "tool_search"].includes(activity.tool));
    expect(operationalIndex, "Operational calls must follow discovery").toBeGreaterThan(searchCompleteIndex);
    expect(completed.map((activity) => activity.tool)).toEqual(expect.arrayContaining(scenario.tools));
    expect(activities.some((activity) => /___(?:create|update|delete)_/.test(activity.tool))).toBe(false);
    expect(answer).toMatch(scenario.answer);

    const timeline = page.getByRole("button").filter({ hasText: /Load Skill|Tool Search|Search Parts|Check Stock|Get Work Order|Knowledge Base/ });
    await expect(timeline.first()).toContainText("Load Skill");
    await expect(page.locator(".markdown-body").last()).toContainText(scenario.answer);
    if (scenario.tools.includes(knowledgeTool)) {
      const knowledgeCalls = activities.filter((activity) => activity.tool.includes("knowledge_base"));
      expect(new Set(knowledgeCalls.map((activity) => activity.call_id)).size,
        "Knowledge retrieval must use one combined call, not reworded retries").toBe(1);
      const knowledgeResult = completed.find((activity) => activity.tool === knowledgeTool);
      expect(JSON.parse(knowledgeResult.args).queries).toHaveLength(1);
      expect(answer).toMatch(/sources/i);
      await expect(page.locator(".sources-section")).toBeVisible();
      await expect(page.locator(".source-pill").first()).toBeVisible();
    }
    await expect(page.getByPlaceholder("Ask Fibey something...")).toBeEnabled();
    expect(browserErrors).toEqual([]);

    const reset = page.waitForResponse((result) => result.url().endsWith("/api/sessions/reset"));
    await page.getByRole("button", { name: /New Chat/ }).click();
    expect((await reset).status()).toBe(200);
    await expect(page.getByRole("button").filter({ hasText: scenario.prompt })).toBeVisible();
  });
}
