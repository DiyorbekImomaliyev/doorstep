// SB-01..SB-03: the seller's sidebar shows live state per item, names each page in a tooltip,
// walks with the arrow keys, and stays a tidy 44 px strip on a phone.
import { expect, test } from "@playwright/test";

let token = "";
test.beforeAll(async ({ request }) => {
  const seed = await (await request.post("/demo/seed?scratch=1")).json();
  token = seed.seller_token as string;
});

const side = (page: import("@playwright/test").Page) => page.locator("aside.sidebar");

test("SB-01 SB-02 each item has a tooltip and live state; urgent is maroon, the rest grey", async ({ page }) => {
  await page.route(`**/api/seller/${token}/autopilot`, (r) => r.fulfill({ json: { rules: [{ id: "r1", failure_point: "FP-01", currency: "GBP", max_amount_minor: 5000, status: "on", precedents: 3, updated_at: "2026-11-12T10:00:00Z" }], suggestions: [] } }));
  await page.route(`**/api/seller/${token}/policy-check`, (r) => r.fulfill({ json: {
    status: "checked", facts: [], rule_id: null, rule_version: null, source: "", disclaimer: "", provider: null, dropped: 0,
    issues: [{ quote: "q", problem: "p", suggestion: "s", facts: [] }, { quote: "q2", problem: "p", suggestion: "s", facts: [] }],
  } }));
  await page.goto(`/seller/${token}`);
  const s = side(page);
  await expect(s.getByRole("button", { name: /^Needs you/ })).toBeVisible();
  const buttons = s.locator(".nav button");
  expect(await buttons.count()).toBe(10);
  for (let i = 0; i < 10; i++) expect((await buttons.nth(i).getAttribute("title")) ?? "").not.toBe("");
  // Policy: a maroon count of issues. Autopilot: an On pill, with no text in the accessible name.
  await expect(s.locator(".nav button", { hasText: "Policy" }).locator(".count.alert")).toHaveText("2");
  await expect(s.locator(".nav button", { hasText: "Policy" })).toHaveAttribute("title", /2 issues found/);
  await expect(s.locator(".nav button", { hasText: "Autopilot" }).locator(".pill-on")).toBeVisible();
  await expect(s.getByRole("button", { name: "Autopilot", exact: true })).toBeVisible();
  // Counts that only inform stay grey: In transit never carries the alert style.
  await expect(s.locator(".nav button", { hasText: "In transit" }).locator(".count")).not.toHaveClass(/alert/);
});

test("SB-02 arrow keys walk the sidebar and the focus ring shows", async ({ page }) => {
  await page.goto(`/seller/${token}`);
  const first = side(page).getByRole("button", { name: /^Needs you/ });
  await first.focus();
  await page.keyboard.press("ArrowDown");
  await expect(side(page).getByRole("button", { name: /^In transit/ })).toBeFocused();
  await page.keyboard.press("ArrowUp");
  await expect(first).toBeFocused();
  await page.keyboard.press("Tab");
  await page.keyboard.press("Shift+Tab");
  const ring = await first.evaluate((e) => getComputedStyle(e).outlineStyle);
  expect(ring).toBe("solid");
});

test("SB-08 the Policy count is maroon until the Policy page shows it, then grey until a check finds a different clause", async ({ page }) => {
  let quote = "Once tracking shows delivered, it is your responsibility.";
  await page.route(`**/api/seller/${token}/policy-check`, (r) => r.fulfill({ json: {
    status: "checked", facts: [], rule_id: null, rule_version: null, source: "", disclaimer: "", provider: null, dropped: 0,
    issues: [{ quote, problem: "p", suggestion: "s", facts: [] }],
  } }));
  const policy = () => side(page).locator(".nav button", { hasText: "Policy" });
  await page.goto(`/seller/${token}`);
  await expect(policy().locator(".count.alert")).toHaveText("1");

  await policy().click();
  await expect(policy().locator(".count")).toHaveText("1");
  await expect(policy().locator(".count.alert")).toHaveCount(0);
  await expect(policy()).toHaveAttribute("title", /1 issue found · seen/);
  await page.goto(`/seller/${token}`);
  await expect(policy().locator(".count")).toHaveText("1");
  await expect(policy().locator(".count.alert")).toHaveCount(0);

  quote = "We are not responsible once the parcel is delivered.";
  await page.reload();
  await expect(policy().locator(".count.alert")).toHaveText("1");
});

test("SB-09 Disputes and Claims are maroon only while they wait on the seller; once sent they stay grey", async ({ page }) => {
  const dispute = (route: string) => ({ dispute_id: "d1", paypal_dispute_id: "PP-D-1", order_id: "o1", case_id: "c1", order_ref: "R1", item: "Headphones",
    reason: "MERCHANDISE_OR_SERVICE_NOT_RECEIVED", amount_minor: 8900, currency: "USD", status: "open", route, protection: null, outcome: null,
    opened_at: "2026-11-12T10:00:00Z", simulated: true });
  const claim = (id: string, status: string) => ({ recovery_id: id, case_id: "c", order_id: "o", order_ref: "R", item: "Jacket", carrier: "royal_mail",
    carrier_name: "Royal Mail", claim_minor: 7500, cover_minor: 7500, currency: "GBP", status });
  let route = "waiting";
  let claims = [claim("r1", "PREPARED"), claim("r2", "SUBMITTED")];
  await page.route(`**/api/seller/${token}/disputes`, (r) => r.fulfill({ json: [dispute(route)] }));
  await page.route(`**/api/seller/${token}/claims`, (r) => r.fulfill({ json: claims }));
  const item = (name: string) => side(page).locator(".nav button", { hasText: name });

  await page.goto(`/seller/${token}`);
  await expect(item("Disputes").locator(".count.alert")).toHaveText("1");
  await expect(item("Disputes")).toHaveAttribute("title", /1 need a reply/);
  await expect(item("Claims").locator(".count.alert")).toHaveText("1");
  await expect(item("Claims")).toHaveAttribute("title", /1 to file · 1 with the carrier/);

  // Evidence sent and the claim filed: both still open, now waiting on PayPal and the carrier.
  route = "evidence";
  claims = [claim("r1", "SUBMITTED"), claim("r2", "SUBMITTED")];
  await page.reload();
  await expect(item("Disputes").locator(".count")).toHaveText("1");
  await expect(item("Disputes").locator(".count.alert")).toHaveCount(0);
  await expect(item("Disputes")).toHaveAttribute("title", /1 with PayPal/);
  await expect(item("Claims").locator(".count")).toHaveText("2");
  await expect(item("Claims").locator(".count.alert")).toHaveCount(0);
});

test.describe("phone", () => {
  test.use({ viewport: { width: 390, height: 844 } });
  test("SB-03 the strip has 44 px targets and the page does not scroll sideways", async ({ page }) => {
    await page.goto(`/seller/${token}`);
    const s = side(page);
    await expect(s.getByRole("button", { name: /^Needs you/ })).toBeVisible();
    const buttons = s.locator(".nav button");
    for (let i = 0; i < (await buttons.count()); i++) {
      const box = await buttons.nth(i).boundingBox();
      expect(box!.height).toBeGreaterThanOrEqual(44);
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
  });
});
