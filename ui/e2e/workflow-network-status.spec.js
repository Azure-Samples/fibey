import { test, expect } from "@playwright/test";
import { randomUUID } from "node:crypto";

let sessionId;

test.beforeEach(() => {
  sessionId = randomUUID();
});

test.afterEach(async ({ request }) => {
  const response = await request.post("/api/sessions/reset", {
    data: { session_id: sessionId },
  });
  expect(response.status()).toBe(200);
});

test("a field briefing includes the identified network element without losing job context", async ({ request }) => {
  const response = await request.post("/api/chat", {
    timeout: 150_000,
    data: {
      session_id: sessionId,
      message: "Give me a full field briefing for WO-007. The network elements for this briefing are NE-3 Access Ring and OLT-NE-201.",
    },
  });
  expect(response.status()).toBe(200);
  const events = (await response.text()).split(/\r?\n\r?\n/).filter((block) => block.trim()).map((block) => {
    const lines = block.split(/\r?\n/);
    const event = lines.find((line) => line.startsWith("event: "))?.slice(7);
    const raw = lines.find((line) => line.startsWith("data: "))?.slice(6);
    return { event, data: raw === "[DONE]" ? raw : JSON.parse(raw) };
  });
  expect(events.filter((event) => event.event === "error")).toEqual([]);
  expect(events.filter((event) => event.event === "done")).toEqual([{ event: "done", data: "[DONE]" }]);
  const activities = events.filter((event) => event.event === "activity").map((event) => event.data);
  expect(activities.filter((activity) => activity.status === "error")).toEqual([]);
  const completed = activities.filter((activity) => activity.status === "complete");
  expect(completed.some((activity) =>
    activity.tool === "load_skill" && JSON.parse(activity.args).skill_name === "field-briefing"
  )).toBe(true);
  expect(completed.map((activity) => activity.tool)).toEqual(expect.arrayContaining([
    "work_orders___get_work_order_work_orders__work_order_id__get",
    "inventory___check_stock_batch",
    "knowledge_base___knowledge_base_retrieve",
    "browser_automation___browser_navigate",
    "browser_automation___close_browser_session",
  ]));
  const answer = events.filter((event) => event.event === "delta").map((event) => event.data.content).join("");
  expect(answer).toMatch(/WO-007/);
  expect(answer).toMatch(/parts|checklist/i);
  expect(answer).toMatch(/safety/i);
  expect(answer).toMatch(/procedure/i);
  expect(answer).toMatch(/NE-3[\s\S]{0,160}\boutage\b/i);
  expect(answer).toMatch(/(?:OLT[- ]NE-201[\s\S]{0,160}\bdegraded\b|\bdegraded\b[\s\S]{0,160}OLT[- ]NE-201)/i);
  expect(answer).toMatch(/sources/i);
  expect(answer).toMatch(/(?:Source:\s*|[-*]\s*(?:📄\s*)?)Status Dashboard\b/);
  expect(answer).not.toMatch(/https?:\/\//);
});
