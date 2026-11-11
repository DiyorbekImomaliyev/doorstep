// AP-10..AP-13 and PL-10..PL-12 in a real browser, against a seeded scratch shop. Every control on
// the Autopilot and Policy pages is clicked, and its result is checked against the real API.
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const NOT_HERE = { data: { reason: "not_received", note: "" } };
const shots = process.env.E2E_SHOTS;
test.setTimeout(120_000);

type Seed = { seller_token: string; buyers: Record<string, string> };
async function seed(request: APIRequestContext): Promise<Seed> {
  return (await request.post("/demo/seed?scratch=1")).json();
}
const desk = async (request: APIRequestContext, t: string) => (await request.get(`/api/seller/${t}/automate/autopilot`)).json();
const lostCard = async (request: APIRequestContext, t: string) => (await desk(request, t)).cards.find((c: { failure_point: string }) => c.failure_point === "CARRIER_LOST");
/** Diyorbek reports his jacket and Malika approves the £60 refund: her third approval, so Doorstep offers the rule. */
async function thirdApproval(request: APIRequestContext, s: Seed) {
  const c = await (await request.post(`/api/buyer/${s.buyers["sam-jacket"]}/not-here`, NOT_HERE)).json();
  expect((await request.post(`/api/seller/${s.seller_token}/cases/${c.case_id}/approve`)).ok()).toBeTruthy();
}
const noOverflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);

test("AP-10 AP-11 AP-12 AP-13 Malika learns, turns on, watches, replays and turns off Autopilot", async ({ page, request }) => {
  const s = await seed(request);
  const t = s.seller_token;
  await page.goto(`/seller/${t}#autopilot`);

  // AP-10: two approvals of three; the lost-parcel rule is still learning and can't be switched on.
  const lost = page.getByLabel("Lost parcels rule");
  await expect(lost).toContainText("Learning");
  await expect(lost).toContainText("2 of 3");
  const api0 = await lostCard(request, t);
  await expect(lost).toContainText(`£${api0.max_amount_minor / 100}`);
  await expect(lost.getByRole("switch", { name: "Lost parcels" })).toBeDisabled();
  await expect(page.getByLabel("Damaged in transit rule")).toContainText("Not offered");
  await expect(page.getByLabel("Autopilot rules")).toContainText("Autopilot is off");

  // AP-12: What if replays the shop's cases; each filter shows the API's own count.
  await lost.getByRole("button", { name: "What if" }).click();
  const wi = page.getByLabel("What if");
  await expect(wi).toBeInViewport();
  const counts = { Rule: 0, Automatic: 0, You: 0 } as Record<string, number>;
  for (const c of api0.replay.cases as { outcome: string }[]) counts[{ rule: "Rule", auto: "Automatic", you: "You" }[c.outcome]!] += 1;
  const grid = wi.getByLabel("Replayed cases");
  for (const [name, n] of Object.entries(counts)) {
    await wi.getByRole("group", { name: "Filter replayed cases" }).getByRole("button", { name: new RegExp(`^${name}`) }).click();
    await expect(grid.locator(".ag-row")).toHaveCount(n);
  }
  await wi.getByRole("group", { name: "Filter replayed cases" }).getByRole("button", { name: /^All/ }).click();
  await expect(grid.locator(".ag-row")).toHaveCount(api0.replay.cases.length);
  await expect(wi.getByText("Rule refunds").first()).toBeVisible();
  // A replayed case opens in the case drawer.
  await grid.locator(".ag-row").first().click();
  await expect(page.locator(".drawer").or(page.getByRole("dialog"))).toBeVisible();
  await page.keyboard.press("Escape");

  // AP-10: the third approval makes it an offer; the switch turns it on through the existing endpoint.
  await thirdApproval(request, s);
  await page.reload();
  await expect(lost).toContainText("Offered");
  await lost.getByRole("switch", { name: "Lost parcels" }).click();
  await expect(lost).toContainText("On");
  await expect(lost.getByRole("switch", { name: "Lost parcels" })).toHaveAttribute("aria-checked", "true");
  expect((await lostCard(request, t)).status).toBe("on");
  await expect(page.getByLabel("Autopilot rules")).toContainText("Autopilot on: lost parcels up to £60");

  // AP-11: Dilnoza's lost beret is refunded by the rule; What it did lists it and opens the case.
  const dilnoza = await (await request.post(`/api/buyer/${s.buyers["priya-beret"]}/not-here`, NOT_HERE)).json();
  expect(dilnoza.status).toBe("AUTO_REFUND");
  const did = page.getByLabel("Autopilot statement");
  await expect(did.getByRole("button", { name: /Wool beret, burgundy/ })).toBeVisible({ timeout: 30_000 }); // the page follows the worker
  await expect(did).toContainText("Last 7 days: 1 automatic refund, £52", { timeout: 60_000 });
  await expect(lost).toContainText("1 · £52");
  if (shots) await page.screenshot({ path: `${shots}/autopilot-on.png`, fullPage: true });
  await did.getByRole("button", { name: /Wool beret, burgundy/ }).click();
  await expect(page.getByLabel("Refunded by your autopilot rule")).toBeVisible();
  await page.keyboard.press("Escape");

  // AP-13: Turn all off waits for Undo; Undo keeps the rule on at the API.
  await page.getByRole("button", { name: "Turn all off" }).click();
  const toast = page.locator(".ax-undo");
  await expect(toast).toContainText("Turning off 1 rule");
  await expect(lost).toContainText("Turning off");
  await toast.getByRole("button", { name: "Undo" }).click();
  await expect(toast).toHaveCount(0);
  await expect(lost).toContainText("On");
  await page.waitForTimeout(6000); // past the undo window: nothing was sent
  expect((await lostCard(request, t)).status).toBe("on");

  // Without Undo, the existing off endpoint runs after the window and the rule is off for good.
  await page.getByRole("button", { name: "Turn all off" }).click();
  await expect(page.getByLabel("Autopilot rules")).toContainText("Autopilot is off");
  await expect.poll(async () => (await lostCard(request, t)).status, { timeout: 15_000 }).toBe("off");
  await expect(lost).toContainText("Off");
  await expect(lost.getByRole("switch", { name: "Lost parcels" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Turn all off" })).toHaveCount(0);
  expect((await (await request.get(`/api/seller/${t}/autopilot`)).json()).rules).toEqual([]);
});

test("AP-10 Not now dismisses the offer; Turn on then the single switch turns it off", async ({ page, request }) => {
  const a = await seed(request);
  await thirdApproval(request, a);
  await page.goto(`/seller/${a.seller_token}#autopilot`);
  const lost = page.getByLabel("Lost parcels rule");
  await lost.getByRole("button", { name: "Not now" }).click();
  await expect(lost).toContainText("Dismissed");
  expect((await lostCard(request, a.seller_token)).status).toBe("dismissed");
  expect((await (await request.get(`/api/seller/${a.seller_token}/autopilot`)).json()).suggestions).toEqual([]);

  const b = await seed(request);
  await thirdApproval(request, b);
  await page.goto(`/seller/${b.seller_token}#autopilot`);
  await lost.getByRole("button", { name: "Turn on" }).click();
  await expect(lost.getByRole("switch", { name: "Lost parcels" })).toHaveAttribute("aria-checked", "true");
  expect((await lostCard(request, b.seller_token)).status).toBe("on");
  await lost.getByRole("switch", { name: "Lost parcels" }).click();
  await expect(page.locator(".ax-undo")).toContainText("Lost parcels: turning off");
  await expect.poll(async () => (await lostCard(request, b.seller_token)).status, { timeout: 15_000 }).toBe("off");
  await expect(lost).toContainText("Off");
});

test("PL-10 PL-11 PL-12 the policy is checked clause by clause; Check again re-runs the check", async ({ page, request, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  const s = await seed(request);
  const t = s.seller_token;
  const policy = await (await request.get(`/api/seller/${t}/automate/policy`)).json();
  await page.goto(`/seller/${t}#policy`);
  const check = page.getByLabel("Policy check");
  await expect(check.getByLabel("Policy text")).toContainText(policy.policy_text.slice(0, 40));
  await expect(page.getByLabel("Summary")).toContainText(policy.rules[0].title);
  await expect(page.getByLabel("Summary")).toContainText(policy.rules[0].version);

  // PL-12: Check again calls the existing check; the page shows what it answered.
  await page.getByRole("button", { name: "Check again" }).click();
  const res = await (await request.get(`/api/seller/${t}/policy-check`)).json(); // cached: the same answer
  const toast = page.locator(".toast");
  if (res.status === "checked") {
    const n = res.issues.length;
    await expect(toast).toContainText(n ? `Checked · ${n} conflict` : "Checked · no conflicts");
    await expect(page.locator(".ax-checked")).toContainText("Checked");
  } else {
    await expect(toast).toContainText("Not checked");
    await expect(check).toContainText("Not checked yet");
  }

  // PL-10/11: each conflict is marked in the text, with its finding, rule file and suggested wording.
  if (res.status === "checked" && res.issues.length) {
    const mark = check.locator("mark.ax-mark").first();
    await expect(mark).toBeVisible();
    await mark.click();
    const f1 = check.getByLabel("Finding 1");
    await expect(f1).toHaveClass(/is-active/);
    await expect(f1).toContainText(res.issues[0].suggestion);
    await expect(f1).toContainText(/High|Medium/);
    const law = policy.rules[0];
    if (res.issues[0].facts.some((f: { source: string }) => f.source === law.id)) {
      await expect(f1.getByRole("link", { name: new RegExp(law.id) })).toHaveAttribute("href", law.url);
    }
    await f1.getByRole("button", { name: "Copy suggested wording 1" }).click();
    await expect(f1).toContainText("Copied");
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(res.issues[0].suggestion);
    // Revised: every marked clause replaced by its suggestion, ready to copy.
    await check.getByRole("group", { name: "Policy view" }).getByRole("button", { name: "Revised" }).click();
    await expect(check.getByLabel("Revised policy").locator("ins").first()).toHaveText(res.issues[0].suggestion);
    await check.getByRole("button", { name: "Copy revised policy" }).click();
    expect(await page.evaluate(() => navigator.clipboard.readText())).toContain(res.issues[0].suggestion);
    await check.getByRole("group", { name: "Policy view" }).getByRole("button", { name: "Original" }).click();
    await expect(check.locator("mark.ax-mark").first()).toBeVisible();
    if (shots) await page.screenshot({ path: `${shots}/policy.png`, fullPage: true });
  }

  // The facts the check compared with, by rule file and version.
  await check.getByText("Checked against").click();
  await expect(check.locator(".ax-facts li")).toHaveCount(res.facts.length);
  await expect(check.locator(".ax-facts")).toContainText(policy.rules.at(-1).id);
});

test.describe("phone", () => {
  test.use({ viewport: { width: 390, height: 844 } });
  test("AP-10 PL-10 both pages fit a phone with their summary first", async ({ page, request }) => {
    const s = await seed(request);
    for (const h of ["autopilot", "policy"]) {
      await page.goto(`/seller/${s.seller_token}#${h}`);
      await expect(page.getByLabel("Summary")).toBeVisible();
      expect(await noOverflow(page), `${h} overflows`).toBe(true);
    }
    await page.goto(`/seller/${s.seller_token}#autopilot`);
    await expect(page.getByLabel("Lost parcels rule")).toBeVisible();
    await expect(page.getByLabel("Replayed cases").locator(".ledger-card").first()).toBeVisible();
  });
});
