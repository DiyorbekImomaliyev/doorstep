// FP-R1: Claims shows the buyer side of the same parcel (a Buyer column and the same fact in the
// claim's story), read from the claims desk's own case state and refunds; a link opens one claim.
import { expect, test, type Page } from "@playwright/test";

type Row = { item: string; case_id: string; order_id: string; recovery_id: string; case_state: string | null; refunded_minor: number };

let token = "";
let rows: Row[] = [];
test.describe.configure({ mode: "serial" });
test.use({ deviceScaleFactor: 2 });

test.beforeAll(async ({ request }) => {
  const seed = await (await request.post("/demo/seed?scratch=1")).json();
  token = seed.seller_token as string;
  await expect.poll(async () => (await (await request.get(`/api/seller/${token}/claims/desk`)).json()).rows.length, { timeout: 30_000 }).toBe(5);
  // The seeded refunds finish in the worker; wait until the three refunded cases say so.
  await expect.poll(async () => {
    rows = (await (await request.get(`/api/seller/${token}/claims/desk`)).json()).rows;
    return rows.filter((r) => r.refunded_minor > 0).length;
  }, { timeout: 30_000 }).toBe(3);
});

const byItem = (item: string) => rows.find((r) => r.item.startsWith(item))!;
const grid = (page: Page) => page.getByLabel("Carrier claims");
const row = (page: Page, item: string) => grid(page).locator(".ag-row:not(.ag-row-pinned):not(.ag-full-width-row)", { hasText: item });
const buyerCell = (page: Page, item: string) => row(page, item).locator('[col-id="buyer"]');
const errors = (page: Page) => {
  const out: string[] = [];
  page.on("console", (m) => { if (m.type() === "error") out.push(m.text()); });
  page.on("pageerror", (e) => out.push(e.message));
  return out;
};

test.describe("desktop", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("FP-R1 the Buyer column reads the case: refunded, or waiting for you with a link to the case", async ({ page }) => {
    const errs = errors(page);
    await page.goto(`/seller/${token}#claims`);
    await expect(grid(page).getByRole("columnheader", { name: "Buyer" })).toBeVisible();
    // The API's own facts decide each label.
    const denim = byItem("Denim jacket");
    expect(["AWAITING_SELLER", "HOLD_FOR_REVIEW"]).toContain(denim.case_state);
    expect(denim.refunded_minor).toBe(0);
    await expect(buyerCell(page, "Denim jacket")).toHaveText("Needs you");
    expect(byItem("Leather belt").refunded_minor).toBeGreaterThan(0);
    await expect(buyerCell(page, "Leather belt")).toHaveText("Refunded");
    await expect(buyerCell(page, "Ceramic mug")).toHaveText("Refunded");
    await expect(buyerCell(page, "Silk scarf")).toHaveText("Refunded");
    // The claim's story says the same, next to the refund amount.
    await row(page, "Leather belt").click();
    const story = page.getByRole("region", { name: "Claim for Leather belt, brown" }).getByLabel("Order and case");
    await expect(story).toContainText("BuyerRefunded");
    await expect(story).toContainText("£58");
    // Needs you is a link to the buyer's case: the same case the Control Room's Needs you shows.
    await buyerCell(page, "Denim jacket").getByRole("button", { name: /^Buyer: Needs you/ }).click();
    await expect(page.locator(".drawer")).toBeVisible();
    await expect(page.locator(".drawer")).toContainText("Denim jacket");
    await page.keyboard.press("Escape");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
    expect(errs).toEqual([]);
  });

  test("FP-R1 a link with ?claim= opens that claim's story, then leaves the address clean", async ({ page }) => {
    const errs = errors(page);
    const silk = byItem("Silk scarf");
    await page.goto(`/seller/${token}?claim=${silk.case_id}#claims`);
    const story = page.getByRole("region", { name: "Claim for Silk scarf, green" });
    await expect(story).toBeVisible();
    await expect(row(page, "Silk scarf")).toHaveClass(/ag-row-selected/);
    await expect.poll(() => page.evaluate(() => window.location.search)).toBe("");
    expect(page.url()).toContain("#claims");
    // The order id works too.
    await page.goto(`/seller/${token}?claim=${byItem("Ceramic mug").order_id}#claims`);
    await expect(page.getByRole("region", { name: "Claim for Ceramic mug set" })).toBeVisible();
    // An id that is not a claim opens the desk as usual.
    await page.goto(`/seller/${token}?claim=nope#claims`);
    await expect(grid(page)).toBeVisible();
    await expect(page.getByRole("region", { name: /^Claim for/ })).toHaveCount(0);
    expect(errs).toEqual([]);
  });
});

test.describe("phone", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("FP-R1 each claim card names the buyer side; a link opens its card", async ({ page }) => {
    const errs = errors(page);
    await page.goto(`/seller/${token}?claim=${byItem("Denim jacket").recovery_id}#claims`);
    const card = page.locator(".ledger-card", { hasText: "Denim jacket" });
    await expect(card).toHaveAttribute("aria-expanded", "true");
    await expect(card).toContainText("BuyerNeeds you");
    await expect(page.locator(".ledger-card", { hasText: "Leather belt" })).toContainText("BuyerRefunded");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
    if (process.env.E2E_SHOTS) await page.screenshot({ path: `${process.env.E2E_SHOTS}/fp-r1-phone.png`, fullPage: true });
    expect(errs).toEqual([]);
  });
});
