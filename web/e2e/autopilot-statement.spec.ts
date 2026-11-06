// AP-07 in a real browser: after Maria turns autopilot on, Priya's lost beret is refunded by her
// rule without waiting for her, and the Control Room accounts for it, linked to the case.
import { expect, test } from "@playwright/test";

const NOT_HERE = { data: { reason: "not_received", note: "" } };

test.setTimeout(120_000);

test("AP-07 the autopilot statement shows what the rule refunded and opens the case", async ({ page, request }) => {
  const seed = await (await request.post("/demo/seed?scratch=1")).json();
  const t = seed.seller_token;
  const sam = await (await request.post(`/api/buyer/${seed.buyers["sam-jacket"]}/not-here`, NOT_HERE)).json();
  expect((await request.post(`/api/seller/${t}/cases/${sam.case_id}/approve`)).ok()).toBeTruthy();
  expect((await request.post(`/api/seller/${t}/autopilot`, { data: { failure_point: "CARRIER_LOST", currency: "GBP" } })).ok()).toBeTruthy();
  const priya = await (await request.post(`/api/buyer/${seed.buyers["priya-beret"]}/not-here`, NOT_HERE)).json();
  expect(priya.status).toBe("AUTO_REFUND");

  // The worker completes the refund at PayPal (simulated for demo orders); then it is in the statement.
  // 60 s: with the whole suite seeding shops in parallel, one worker drains a long job queue.
  await expect.poll(async () => (await (await request.get(`/api/seller/${t}/autopilot/statement`)).json()).cases.length, { timeout: 60_000 }).toBe(1);

  await page.goto(`/seller/${t}`);
  const s = page.getByLabel("Autopilot statement");
  await expect(s).toContainText("Last 7 days: 1 automatic refund, £52");
  await s.getByRole("button", { name: /Wool beret, burgundy/ }).click();
  await expect(page.getByLabel("Refunded by your autopilot rule")).toBeVisible();
});
