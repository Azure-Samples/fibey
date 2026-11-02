import { test, expect } from "@playwright/test";

for (const citation of [
  "Source: Status Dashboard",
  "Source: [Status Dashboard](https://status.example/)",
  "- 📄 Status Dashboard",
  "- 📄 [Status Dashboard](https://status.example/)\n- 📄 Installation Standards",
]) {
  test(`dashboard attribution uses source-only pills: ${citation}`, async ({ page }) => {
    const events = [
      ["delta", { content: `NE-3 has an outage.\n\n---\n**Sources**\n- 📄 Safety Protocols\n\n${citation}` }],
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
    await page.getByPlaceholder("Ask Fibey something...").fill("Brief me with network status.");
    await page.getByPlaceholder("Ask Fibey something...").press("Enter");

    await expect(page.locator(".source-pill").filter({ hasText: "Safety Protocols" })).toHaveCount(1);
    await expect(page.locator(".source-pill").filter({ hasText: "Status Dashboard" })).toHaveText("descriptionStatus Dashboard");
    await expect(page.locator('a[href="https://status.example/"]')).toHaveCount(0);
    await expect(page.getByText("https://status.example/", { exact: true })).toHaveCount(0);
    await expect(page.locator(".source-pill").filter({ hasText: /https?:\/\// })).toHaveCount(0);
    await expect(page.locator(".markdown-body").last()).toHaveText("NE-3 has an outage.");
  });
}

test("related browser calls form one site-only activity without grouping other tools", async ({ page }) => {
  const browserTools = [
    "browser_automation___create_browser_session",
    "browser_automation___browser_navigate",
    "browser_automation___browser_snapshot",
    "browser_automation___close_browser_session",
  ];
  const stockTool = "inventory___check_stock";
  const events = [
    ["activity", {
      tool: "tool_search", call_id: "core-search", status: "running",
      args: '{"query":"check stock for parts","limit":10}',
    }],
    ["activity", {
      tool: "tool_search", call_id: "core-search", status: "complete",
      results: [{ name: stockTool, description: "Check part availability" }],
    }],
    ["activity", {
      tool: "tool_search", call_id: "search-call", status: "running",
      args: '{"query":"browser create_browser_session browser_navigate browser_snapshot close_browser_session","limit":10}',
    }],
    ["activity", {
      tool: "tool_search", call_id: "search-call", status: "complete",
      results: browserTools.map((name) => ({ name, description: "Internal browser operation" })),
    }],
    ...browserTools.flatMap((tool, i) => [
      ["activity", {
        tool, call_id: `browser-call-${i}`, status: "running",
        detail: "Status Dashboard",
        args: '{"url":"https://status.example/","browserSessionId":"private-session","depth":4}',
      }],
      ["activity", {
        tool, call_id: `browser-call-${i}`, status: "complete",
        detail: "Status Dashboard",
        result: "https://browser.example/live?token=private-session",
      }],
      ...(i === 1 ? [
        ["activity", { tool: stockTool, call_id: "stock-call", status: "running" }],
        ["activity", { tool: stockTool, call_id: "stock-call", status: "complete" }],
      ] : []),
    ]),
    ["delta", { content: "NE-3 has an outage. [Status Dashboard](https://status.example/)" }],
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
  await page.getByPlaceholder("Ask Fibey something...").fill("Any alerts on NE-3?");
  await page.getByPlaceholder("Ask Fibey something...").press("Enter");

  const activity = page.getByRole("button", { name: "Browse Status Dashboard: Done", exact: true });
  await expect(activity).toHaveCount(1);
  await expect(page.getByRole("button", { name: "Check Stock: Done", exact: true })).toHaveCount(1);
  await activity.click();
  await expect(page.getByText("Browser Automation")).toBeVisible();
  await expect(page.getByText("Status Dashboard", { exact: true }).last()).toBeVisible();
  const discovery = page.getByRole("button", { name: "Tool Search: Done", exact: true });
  await expect(discovery).toHaveCount(1);
  await discovery.click();
  await expect(page.getByText("check stock for parts", { exact: true })).toBeVisible();
  await expect(page.getByText("browser create_browser_session browser_navigate browser_snapshot close_browser_session", { exact: true })).toBeVisible();
  await expect(page.getByText("2 selected", { exact: true })).toBeVisible();
  await expect(page.getByText(/browserSessionId|private-session|depth:|Internal browser operation/)).toHaveCount(0);
  await expect(page.getByText("https://status.example/", { exact: true })).toHaveCount(0);
  await expect(page.locator('a[href="https://status.example/"]')).toHaveCount(0);
  await expect(page.locator(".source-pill").last()).toContainText("Status Dashboard");
  await expect(page.getByPlaceholder("Ask Fibey something...")).toBeEnabled();
});

test("closing a session without reading the dashboard is not a successful browse", async ({ page }) => {
  const events = [
    ...["create_browser_session", "close_browser_session"].flatMap((operation) =>
      ["running", "complete"].map((status) => ["activity", {
        tool: `browser_automation___${operation}`, call_id: operation, status,
      }]),
    ),
    ["delta", { content: "I couldn't read the Status Dashboard." }],
    ["done", "[DONE]"],
  ];
  await page.route("**/api/chat", (route) => route.fulfill({
    status: 200, contentType: "text/event-stream",
    body: events.map(([event, data]) =>
      `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`
    ).join(""),
  }));
  await page.goto("/");
  await page.getByPlaceholder("Ask Fibey something...").fill("Any NE-3 alerts?");
  await page.getByPlaceholder("Ask Fibey something...").press("Enter");
  await expect(page.getByRole("button", { name: "Browse Status Dashboard: Error", exact: true })).toHaveCount(1);
  await expect(page.getByRole("button", { name: "Browse Status Dashboard: Done", exact: true })).toHaveCount(0);
});

test("grouped discovery retains a failed query even after another query succeeds", async ({ page }) => {
  const events = [
    ["activity", { tool: "tool_search", call_id: "failed-search", status: "running", args: '{"query":"work order tools"}' }],
    ["activity", { tool: "tool_search", call_id: "failed-search", status: "error", result: "Discovery unavailable" }],
    ["activity", { tool: "tool_search", call_id: "browser-search", status: "running", args: '{"query":"browser lifecycle"}' }],
    ["activity", { tool: "tool_search", call_id: "browser-search", status: "complete", results: [] }],
    ["delta", { content: "The work order tools could not be retrieved." }],
    ["done", "[DONE]"],
  ];
  await page.route("**/api/chat", (route) => route.fulfill({
    status: 200, contentType: "text/event-stream",
    body: events.map(([event, data]) =>
      `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`
    ).join(""),
  }));
  await page.goto("/");
  await page.getByPlaceholder("Ask Fibey something...").fill("Brief me with network status.");
  await page.getByPlaceholder("Ask Fibey something...").press("Enter");
  const discovery = page.getByRole("button", { name: "Tool Search: Error", exact: true });
  await expect(discovery).toHaveCount(1);
  await expect(page.getByRole("button", { name: "Tool Search: Done", exact: true })).toHaveCount(0);
  await discovery.click();
  await expect(page.getByText("work order tools", { exact: true })).toBeVisible();
  await expect(page.getByText("browser lifecycle", { exact: true })).toBeVisible();
  await expect(page.getByText("Discovery unavailable", { exact: true }).first()).toBeVisible();
});

test("the browser group stays running between steps, retains failures, and resets next turn", async ({ page }) => {
  await page.addInitScript(() => {
    const fetch = window.fetch.bind(window);
    window.fetch = (input, init) => {
      if (input !== "/api/chat") return fetch(input, init);
      return Promise.resolve(new Response(new ReadableStream({
        start(controller) {
          window.emitChatEvents = (events, close) => {
            const body = events.map(([event, data]) =>
              `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`
            ).join("");
            controller.enqueue(new TextEncoder().encode(body));
            if (close) controller.close();
          };
        },
      }), { headers: { "Content-Type": "text/event-stream" } }));
    };
  });
  const publish = (events, close = false) => page.evaluate(
    ({ events, close }) => window.emitChatEvents(events, close), { events, close },
  );
  const browserEvent = (operation, status) => ["activity", {
    tool: `browser_automation___${operation}`,
    call_id: operation,
    status,
    detail: "Status Dashboard",
    result: status === "error" ? "https://browser.example/live?token=private-session" : "",
  }];
  await page.goto("/");
  const input = page.getByPlaceholder("Ask Fibey something...");
  await input.fill("Any alerts on NE-3?");
  await input.press("Enter");
  await expect(input).toBeDisabled();
  await publish([
    browserEvent("create_browser_session", "running"),
  ]);
  const running = page.getByRole("button", { name: "Browse Status Dashboard: Running", exact: true });
  await expect(running).toHaveCount(1);
  await expect(page.getByRole("button", { name: "Browse Status Dashboard: Done", exact: true })).toHaveCount(0);
  await running.click();
  await expect(page.getByText("Browser Automation")).toBeVisible();
  await publish([browserEvent("create_browser_session", "complete")]);
  await expect(running).toHaveCount(1);
  await expect(page.getByText("Browser Automation")).toBeVisible();
  await publish([
    browserEvent("browser_snapshot", "running"),
    browserEvent("browser_snapshot", "complete"),
  ]);
  await expect(running).toHaveCount(1);
  await expect(page.getByText("Browser Automation")).toBeVisible();
  await publish([
    browserEvent("browser_navigate", "running"),
    browserEvent("browser_navigate", "error"),
    browserEvent("close_browser_session", "running"),
    browserEvent("close_browser_session", "complete"),
    ["delta", { content: "I couldn't read the Status Dashboard." }],
    ["done", "[DONE]"],
  ], true);
  await expect(page.getByRole("button", { name: "Browse Status Dashboard: Error", exact: true })).toHaveCount(1);
  await expect(page.getByText("Could not read Status Dashboard", { exact: true })).toBeVisible();
  await expect(page.getByText("Browser Automation")).toBeVisible();
  await expect(page.getByText(/private-session/)).toHaveCount(0);
  await expect(input).toBeEnabled();

  await input.fill("Try the dashboard again.");
  await input.press("Enter");
  await expect(input).toBeDisabled();
  await publish([
    browserEvent("browser_snapshot", "running"),
    browserEvent("browser_snapshot", "complete"),
    ["delta", { content: "The dashboard is available now." }],
    ["done", "[DONE]"],
  ], true);
  await expect(page.getByRole("button", { name: "Browse Status Dashboard: Done", exact: true })).toHaveCount(1);
  await expect(page.getByRole("button", { name: "Browse Status Dashboard: Error", exact: true })).toHaveCount(0);
  await expect(input).toBeEnabled();
});

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
