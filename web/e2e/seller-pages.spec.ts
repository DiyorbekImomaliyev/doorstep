// NV-01..NV-04, MN-01, DS-01 in a real browser: the seller's sidebar has a page for claims, money,
// disputes, autopilot and policy, each a grid on a seeded scratch shop; a hash opens a page.
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const shots = process.env.E2E_SHOTS;

// One private demo shop for the file: every test only reads it (the seed is rate-limited).
let token = "";
test.beforeAll(async ({ request }) => {
  const seed = await (await request.post("/demo/seed?scratch=1")).json();
  token = seed.seller_token as string;
  // A demo auto-refund runs in the worker; wait until the ledger has a row to read.
  await expect.poll(async () => (await (await request.get(`/api/seller/${token}/money`)).json()).length, { timeout: 30_000 }).toBeGreaterThan(0);
});
async function shop(_request: APIRequestContext) { return token; }

const noOverflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);

test("NV-01 the sidebar lists the pages in order", async ({ page, request }) => {
  const t = await shop(request);
  await page.goto(`/seller/${t}`);
  const side = page.locator("aside.sidebar");
  await expect(side.getByRole("button", { name: /^Claims/ })).toBeVisible();
  const labels = await side.locator(".nav-label").allTextContents();
  expect(labels).toEqual(["Orders", "Resolve", "Automate", "Analytics"]);
  const buttons = (await side.locator(".nav button").allTextContents()).map((s) => s.replace(/\d+$/, "").trim());
  expect(buttons).toEqual(["Needs you", "In transit", "Cases", "All orders", "Claims", "Disputes", "Money", "Autopilot", "Policy", "Insights"]);
  await expect(side.getByRole("link", { name: "How Doorstep works" })).toBeVisible();
});

test("NV-01 CL-01 Claims lists carrier claims and opens the case", async ({ page, request }) => {
  const t = await shop(request);
  await page.goto(`/seller/${t}#claims`);
  await expect(page.getByRole("heading", { name: "Claims" })).toBeVisible();
  const grid = page.getByLabel("Carrier claims");
  await expect(grid.locator(".ag-row:not(.ag-row-pinned)").first()).toBeVisible();
  await expect(grid.locator(".ag-row-pinned").first()).toContainText("Total");
  await expect(page.getByLabel("Summary")).toContainText("Due soon");
  await grid.locator(".ag-row:not(.ag-row-pinned)").first().click();
  await expect(page.getByRole("dialog").or(page.locator(".drawer"))).toBeVisible();
  if (shots) { await page.keyboard.press("Escape"); await page.waitForTimeout(300); await page.screenshot({ path: `${shots}/claims.png` }); }
});

test("MN-01 Money shows each action; a demo refund reads Simulated", async ({ page, request }) => {
  const t = await shop(request);
  await page.goto(`/seller/${t}#money`);
  await expect(page.getByRole("heading", { name: "Money" })).toBeVisible();
  const grid = page.getByLabel("Money actions");
  await expect(grid.locator(".ag-row:not(.ag-row-pinned)").first()).toBeVisible();
  await expect(grid).toContainText("Simulated");
  await expect(grid.locator(".ag-row-pinned").first()).toContainText("Refunded");
  if (shots) await page.screenshot({ path: `${shots}/money.png` });
});

test("DS-01 Disputes lists the PayPal dispute with its Doorstep route", async ({ page, request }) => {
  const t = await shop(request);
  await page.goto(`/seller/${t}#disputes`);
  await expect(page.getByRole("heading", { name: "Disputes" })).toBeVisible();
  const grid = page.getByLabel("PayPal disputes");
  await expect(grid.locator(".ag-row:not(.ag-row-pinned)").first()).toBeVisible();
  await expect(grid).toContainText("SIM-DISPUTE");
  await expect(page.locator(".nav button", { hasText: "Disputes" }).locator(".count")).toHaveText("1");
  if (shots) await page.screenshot({ path: `${shots}/disputes.png` });
});

test("NV-02 NV-03 Autopilot and Policy have their own pages", async ({ page, request }) => {
  const t = await shop(request);
  await page.goto(`/seller/${t}#autopilot`);
  await expect(page.getByRole("heading", { name: "Autopilot" })).toBeVisible();
  await expect(page.getByLabel("Autopilot rules")).toContainText("Autopilot is off");
  await page.goto(`/seller/${t}#policy`);
  await expect(page.getByRole("heading", { name: "Policy" })).toBeVisible();
  await expect(page.getByLabel("Policy check")).toBeVisible();
});

test("NV-04 the Control Room greeting has no count and the suggestion is one slim line", async ({ page, request }) => {
  const t = await shop(request);
  await page.goto(`/seller/${t}`);
  await expect(page.locator(".greet")).toHaveText(/^Good (morning|afternoon|evening), Malika$/);
  await expect(page.getByLabel("Summary")).toBeVisible();
  const tip = page.getByLabel("Prevention suggestion");
  if (await tip.count()) expect((await tip.boundingBox())!.height).toBeLessThan(56);
  if (shots) { await page.waitForTimeout(600); await page.screenshot({ path: `${shots}/control-room.png` }); }
});

test("NV-01 a plain hash link and hashchange switch pages; the command bar lists them", async ({ page, request }) => {
  const t = await shop(request);
  await page.goto(`/seller/${t}`);
  await page.evaluate(() => { location.hash = "#claims"; });
  await expect(page.getByRole("heading", { name: "Claims" })).toBeVisible();
  await page.evaluate(() => { location.hash = "#money"; });
  await expect(page.getByRole("heading", { name: "Money" })).toBeVisible();
  await page.evaluate(() => { location.hash = ""; });
  await expect(page.getByRole("heading", { name: "Delivery Control Room" })).toBeVisible();
  await page.keyboard.press("Control+k");
  for (const name of ["Claims", "Disputes", "Money", "Autopilot", "Policy"]) {
    await expect(page.locator(".cmdk, [role=dialog]").getByText(name, { exact: true }).first()).toBeVisible();
  }
});

test.describe("phone", () => {
  test.use({ viewport: { width: 390, height: 844 } });
  test("NV-01 the new pages fit a phone without sideways scrolling", async ({ page, request }) => {
    const t = await shop(request);
    for (const h of ["claims", "money", "disputes", "autopilot", "policy"]) {
      await page.goto(`/seller/${t}#${h}`);
      await expect(page.locator(".main h1")).toBeVisible();
      if (["claims", "money", "disputes"].includes(h)) await expect(page.locator(".ledger-card").first()).toBeVisible();
      expect(await noOverflow(page), `${h} overflows`).toBe(true);
    }
    await page.goto(`/seller/${t}`);
    await page.locator("aside.sidebar").getByRole("button", { name: /^Claims/ }).click();
    await expect(page.getByRole("heading", { name: "Claims" })).toBeVisible();
  });
});
