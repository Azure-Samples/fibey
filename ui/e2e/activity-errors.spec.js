import { test, expect } from "@playwright/test";

test("a failed knowledge call stays one activity and exposes its error", async ({ page }) => {
  const tool = "knowledge_base___knowledge_base_retrieve";
  const args = JSON.stringify({ queries: ["What are the splicing safety protocols?"] });
  const events = [
    ["activity", { tool: "call_tool", call_id: "kb-call", status: "running" }],
    ["activity", { tool, call_id: "kb-call", status: "running", args }],
    ["activity", {
      tool,
      call_id: "kb-call",
      status: "error",
      args,
      detail: "Knowledge retrieval failed",
      result: "Error: Function failed.",
    }],
    ["delta", { content: "Knowledge retrieval is unavailable." }],
    ["done", "[DONE]"],
  ];
  await page.route("**/api/chat", (route) => route.fulfill({
    status: 200,
    contentType: "text/event-stream",
    body: events.map(([event, data]) =>
      `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`
    ).join(""),
  }));

  await page.goto("/");
  await page.getByRole("button").filter({
    hasText: "What are the proper procedures for fusion splicing single-mode fiber?",
  }).click();

  const activity = page.getByRole("button", { name: "Knowledge Base: Error", exact: true });
  await expect(activity).toHaveCount(1);
  await expect(page.getByRole("button", { name: "Knowledge Base: Done", exact: true })).toHaveCount(0);
  await activity.click();
  await expect(page.getByText("Tool call failed", { exact: true })).toBeVisible();
  await expect(page.getByText("Error: Function failed.", { exact: true })).toBeVisible();
  await expect(page.getByPlaceholder("Ask Fibey something...")).toBeEnabled();
});
