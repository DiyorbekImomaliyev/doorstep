// CL-02..CL-07 in a real browser: the claims desk on a seeded scratch shop. Every control is
// clicked and its real result read back from the page and from the API: KPIs, the claim clock,
// the status filter, the story under a row, Copy, Open in case, each recovery mark, carrier grouping.
import { expect, test, type Locator, type Page } from "@playwright/test";

const shots = process.env.E2E_SHOTS;
/** Desktop shots are the viewport, like the Control Room's; phone shots are the whole page. */
const shot = async (page: Page, name: string, at?: Locator) => {
  if (!shots) return;
  const phone = (page.viewportSize()?.width ?? 1440) < 600;
  if (at) await at.evaluate((el) => el.scrollIntoView({ block: "start" }));
  await page.waitForTimeout(250);
  await page.screenshot({ path: `${shots}/${name}.png`, fullPage: phone });
};
const noOverflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);

let token = "";
test.describe.configure({ mode: "serial" });
test.use({ permissions: ["clipboard-read", "clipboard-write"], deviceScaleFactor: 2 });

test.beforeAll(async ({ request }) => {
  const seed = await (await request.post("/demo/seed?scratch=1")).json();
  token = seed.seller_token as string;
  await expect.poll(async () => (await (await request.get(`/api/seller/${token}/claims/desk`)).json()).rows.length, { timeout: 30_000 }).toBe(5);
});

const desk = (page: Page) => page.goto(`/seller/${token}#claims`);
const grid = (page: Page) => page.getByLabel("Carrier claims");
const row = (page: Page, item: string) => grid(page).locator(".ag-row:not(.ag-row-pinned):not(.ag-full-width-row)", { hasText: item });
const seg = (page: Page, name: string) => page.getByRole("radiogroup", { name: "Status" }).getByRole("radio", { name: new RegExp(`^${name}`) });
const segCount = (page: Page, name: string) => seg(page, name).locator(".cd-seg-n");
const story = (page: Page, item: string) => page.getByRole("region", { name: `Claim for ${item}` });
const badge = (page: Page) => page.locator("aside.sidebar .nav button", { hasText: "Claims" }).locator(".count");
const statusOn = async (page: Page, item: string) => (await row(page, item).locator(".badge").first().textContent())?.trim();
async function apiRow(page: Page, item: string) {
  const d = await (await page.request.get(`/api/seller/${token}/claims/desk`)).json();
  return d.rows.find((r: { item: string }) => r.item === item);
}

test.describe("desktop", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("CL-02 the KPIs read the shop's claims, money per currency", async ({ page }) => {
    await desk(page);
    const kpis = page.getByLabel("Summary");
    await expect(kpis).toContainText("Open3£170 · 3 ready");
    await expect(kpis).toContainText("Recovered£581 of 1 won · 100%");
    await expect(kpis).toContainText("Due in 7 days0nothing urgent");
    await expect(segCount(page, "All")).toHaveText("5");
    await expect(segCount(page, "Ready")).toHaveText("3");
    await expect(segCount(page, "Withdrawn")).toHaveText("1");
    await expect(page.locator(".live")).toContainText("Live");
    // Live: while a claim is open the desk reads the server again on its own.
    await page.waitForRequest((r) => r.url().includes("/claims/desk"), { timeout: 9000 });
    await page.waitForTimeout(400);
    await shot(page, "cl-desktop-desk");
  });

  test("CL-03 the claim clock places each open claim; a click opens its row", async ({ page }) => {
    await desk(page);
    const clock = page.getByLabel("Claim clock");
    const pills = clock.locator(".cd-pill");
    await expect(pills).toHaveCount(3);
    // Soonest deadline furthest left.
    const xs = await Promise.all(["Denim jacket", "Silk scarf"].map(async (n) => (await pills.filter({ hasText: n }).boundingBox())!.x));
    expect(xs[0]).toBeLessThan(xs[1]);
    await pills.filter({ hasText: "Silk scarf" }).click();
    await expect(pills.filter({ hasText: "Silk scarf" })).toHaveAttribute("aria-pressed", "true");
    await expect(story(page, "Silk scarf, green")).toBeVisible();
    await expect(row(page, "Silk scarf").locator(".expander")).toHaveAttribute("aria-expanded", "true");
    await expect(row(page, "Silk scarf")).toHaveClass(/ag-row-selected/);
    await shot(page, "cl-desktop-clock-pick");
  });

  test("CL-02 Due in 7 days counts a ready claim near its deadline and opens it", async ({ page }) => {
    // The seeded deadlines are 10 days out or more: bring the scarf's to two days from the server's now.
    await page.route("**/claims/desk", async (route) => {
      const res = await route.fetch();
      const body = await res.json();
      const now = Date.parse(body.now);
      for (const r of body.rows) if (String(r.item).startsWith("Silk scarf")) r.deadline_at = new Date(now + 2 * 86_400_000 - 60_000).toISOString();
      await route.fulfill({ response: res, json: body });
    });
    await desk(page);
    const due = page.getByRole("button", { name: /^Due in 7 days/ });
    await expect(due).toContainText("1£55 · next");
    await expect(due).toHaveClass(/attention/);
    await expect(page.getByLabel("Claim clock").locator(".cd-pill.u-bad")).toContainText("Silk scarf");
    await shot(page, "cl-desktop-due");
    await due.click();
    await expect(story(page, "Silk scarf, green")).toBeVisible();
    await expect(story(page, "Silk scarf, green").getByLabel("Deadline")).toContainText("2 days left");
  });

  test("CL-03 the status filter shows only that status, with live counts", async ({ page }) => {
    await desk(page);
    await seg(page, "Ready").click();
    await expect(seg(page, "Ready")).toHaveAttribute("aria-checked", "true");
    await expect(grid(page).locator(".ag-row:not(.ag-row-pinned):not(.ag-full-width-row)")).toHaveCount(3);
    await expect(grid(page)).toContainText("3 of 5");
    await seg(page, "Withdrawn").click();
    await expect(grid(page).locator(".ag-row:not(.ag-row-pinned):not(.ag-full-width-row)")).toHaveCount(1);
    await expect(row(page, "Cord trousers")).toBeVisible();
    await expect(grid(page).locator(".ag-row-pinned")).toHaveCount(0); // a claim never made has no total
    await seg(page, "Refused").click();
    await expect(grid(page)).toContainText("No refused claims");
    await seg(page, "All").click();
    await expect(grid(page).locator(".ag-row-pinned").first()).toContainText("Total GBP");
  });

  test("CL-04 a row opens its story: pack with Copy, carrier, deadline, history, order and case", async ({ page }) => {
    await desk(page);
    await row(page, "Denim jacket").click();
    const s = story(page, "Denim jacket, size S");
    await expect(s).toBeVisible();
    await expect(s.getByLabel("Claim pack").locator("li").first()).toContainText("The buyer paid £75");
    // A long pack shows its first six sentences, then all of them.
    const total = (await apiRow(page, "Denim jacket, size S")).sentences.length;
    expect(total).toBeGreaterThan(6);
    await expect(s.getByLabel("Claim pack").locator("li")).toHaveCount(6);
    await s.getByRole("button", { name: `All ${total}` }).click();
    await expect(s.getByLabel("Claim pack").locator("li")).toHaveCount(total);
    await expect(s.getByRole("button", { name: "Fewer" })).toHaveAttribute("aria-expanded", "true");
    await expect(s.getByLabel("Deadline")).toContainText("days left");
    await expect(s.getByLabel("Deadline")).toContainText("14-day window");
    await expect(s.getByLabel("Deadline")).toContainText("carrier_claims_v1");
    await expect(s.getByLabel("History")).toContainText("Prepared");
    await expect(s.getByLabel("History")).toContainText("Next");
    await expect(s.getByLabel("Order and case")).toContainText("£75 · Lost");
    await s.getByRole("button", { name: "Copy claim" }).click();
    await expect(s.getByRole("button", { name: "Copied" })).toBeVisible();
    expect(await page.evaluate(() => navigator.clipboard.readText())).toContain("The buyer paid £75");
    await page.waitForTimeout(300);
    await shot(page, "cl-desktop-story", row(page, "Denim jacket"));
    // A paid claim's history says when and how much.
    await row(page, "Leather belt").click();
    await expect(story(page, "Leather belt, brown").getByLabel("History")).toContainText("Paid £58");
    await expect(story(page, "Denim jacket, size S")).toHaveCount(0); // one story open at a time
    await story(page, "Leather belt, brown").getByRole("button", { name: "Open in case" }).click();
    await expect(page.locator(".drawer")).toBeVisible();
    await expect(page.locator(".drawer")).toContainText("Leather belt");
    await page.keyboard.press("Escape");
    await expect(page.locator(".drawer")).toHaveCount(0);
  });
});

test.describe("phone", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("CL-07 the desk fits a phone: KPIs, clock, filter and cards that open in place", async ({ page }) => {
    await desk(page);
    await expect(page.getByLabel("Summary")).toBeVisible();
    await expect(page.getByLabel("Claim clock").locator(".cd-pill")).toHaveCount(3);
    await expect(page.locator(".ledger-card").first()).toBeVisible();
    expect(await noOverflow(page)).toBe(true);
    await shot(page, "cl-phone-desk");
    const card: Locator = page.locator(".ledger-card", { hasText: "Silk scarf" });
    await card.click();
    await expect(card).toHaveAttribute("aria-expanded", "true");
    const s = story(page, "Silk scarf, green");
    await expect(s.getByRole("button", { name: "I submitted it" })).toBeVisible();
    await expect(s.getByLabel("Claim pack").locator("li").first()).toContainText("The buyer paid £55");
    expect(await noOverflow(page)).toBe(true);
    await shot(page, "cl-phone-story");
    await page.getByLabel("Claim clock").locator(".cd-pill").first().click();
    await expect(page.locator(".ledger-card", { hasText: "Denim jacket" })).toHaveAttribute("aria-expanded", "true");
    await seg(page, "Withdrawn").click();
    await expect(page.locator(".ledger-card")).toHaveCount(1);
    await expect(page.locator(".ledger-card")).toContainText("Cord trousers");
    expect(await noOverflow(page)).toBe(true);
  });
});

test.describe("desktop actions", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("CL-05 I submitted it, Carrier paid with the amount, Carrier refused, Withdraw: each saves and refreshes", async ({ page }) => {
    await desk(page);
    await expect(badge(page)).toHaveText("3");
    // Row action: one tap marks it submitted.
    await row(page, "Denim jacket").getByRole("button", { name: "I submitted it" }).click();
    await expect(page.locator(".toast")).toContainText("marked submitted");
    await expect.poll(() => statusOn(page, "Denim jacket")).toBe("Submitted");
    await expect(segCount(page, "Ready")).toHaveText("2");
    await expect(segCount(page, "Submitted")).toHaveText("1");
    await expect(row(page, "Denim jacket").locator(".cd-due")).toHaveText("Filed");
    await expect(page.getByLabel("Claim clock").locator(".cd-pill.u-filed")).toHaveCount(1);
    expect((await apiRow(page, "Denim jacket, size S")).status).toBe("SUBMITTED");
    // Carrier paid needs an amount up to the claim.
    await row(page, "Denim jacket").getByRole("button", { name: "Carrier paid" }).click();
    const s = story(page, "Denim jacket, size S");
    const amount = s.getByLabel("Amount the carrier paid");
    await expect(amount).toBeFocused();
    await expect(amount).toHaveValue("75.00");
    await amount.fill("90");
    await s.getByRole("button", { name: "Save" }).click();
    await expect(s).toContainText("Up to £75");
    await shot(page, "cl-desktop-paid-form", row(page, "Denim jacket"));
    await amount.fill("40");
    await s.getByRole("button", { name: "Save" }).click();
    await expect(page.locator(".toast")).toContainText("£40 recovered");
    await expect.poll(() => statusOn(page, "Denim jacket")).toBe("Paid");
    await expect(page.getByLabel("Summary")).toContainText("Recovered£982 of 2 won · 74%");
    await expect(s.getByLabel("History")).toContainText("Paid £40");
    expect((await apiRow(page, "Denim jacket, size S")).recovered_minor).toBe(4000);

    // The story's own buttons: submit, then the carrier refuses (asked once more first).
    await row(page, "Ceramic mug").click();
    const mug = story(page, "Ceramic mug set");
    await mug.getByRole("button", { name: "I submitted it" }).click();
    await expect(mug.getByRole("button", { name: "Carrier refused" })).toBeVisible();
    await mug.getByRole("button", { name: "Carrier refused" }).click();
    await mug.getByRole("button", { name: "Keep" }).click();
    await expect.poll(() => statusOn(page, "Ceramic mug")).toBe("Submitted");
    await mug.getByRole("button", { name: "Carrier refused" }).click();
    await mug.getByRole("button", { name: "Yes, refused" }).click();
    await expect(page.locator(".toast")).toContainText("marked refused");
    await expect.poll(() => statusOn(page, "Ceramic mug")).toBe("Refused");
    await expect(segCount(page, "Refused")).toHaveText("1");
    await expect(page.getByLabel("Summary")).toContainText("2 of 3 won · 57%");
    await expect(badge(page)).toHaveText("1");

    // Withdraw the last ready claim.
    await row(page, "Silk scarf").click();
    const scarf = story(page, "Silk scarf, green");
    await scarf.getByRole("button", { name: "Withdraw" }).click();
    await scarf.getByRole("button", { name: "Yes, withdraw" }).click();
    await expect(page.locator(".toast")).toContainText("claim withdrawn");
    await expect.poll(() => statusOn(page, "Silk scarf")).toBe("Withdrawn");
    await expect(segCount(page, "Withdrawn")).toHaveText("2");
    await expect(segCount(page, "Ready")).toHaveText("0");
    await expect(page.getByLabel("Summary")).toContainText("Open0none waiting");
    await expect(page.getByLabel("Claim clock")).toContainText("No open claims");
    await expect(badge(page)).toHaveCount(0);
    expect((await apiRow(page, "Silk scarf, green")).history.at(-1)).toMatchObject({ status: "WITHDRAWN", by: "you" });
    // The Control Room's Recovered card has the carrier's payment too.
    await page.locator("aside.sidebar").getByRole("button", { name: /^All orders/ }).click();
    await expect(page.getByRole("button", { name: /Recovered from carriers/ })).toContainText("£98");
  });

  test("CL-06 By carrier groups the claims with won ÷ claimed and money per carrier", async ({ page }) => {
    await desk(page);
    const toggle = page.getByRole("button", { name: "By carrier", exact: true });
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-pressed", "true");
    const groups = grid(page).locator(".cd-group");
    await expect(groups).toHaveCount(2);
    await expect(groups.filter({ hasText: "Royal Mail" })).toContainText("1 of 1 won");
    await expect(groups.filter({ hasText: "Royal Mail" })).toContainText("£58 of £58 · 100%");
    await expect(groups.filter({ hasText: "Shippo (test)" })).toContainText("1 of 2 won");
    await expect(groups.filter({ hasText: "Shippo (test)" })).toContainText("£40 of £115 · 35%");
    await expect(grid(page).locator(".ag-row-pinned").first()).toContainText("Total GBP");
    await shot(page, "cl-desktop-by-carrier", page.getByLabel("Claim clock"));
    await toggle.click();
    await expect(groups).toHaveCount(0);
    // The Recovered card shows the paid claims, and again everything.
    const recovered = page.getByRole("button", { name: /^Recovered/ });
    await recovered.click();
    await expect(seg(page, "Paid")).toHaveAttribute("aria-checked", "true");
    await expect(grid(page).locator(".ag-row:not(.ag-row-pinned):not(.ag-full-width-row)")).toHaveCount(2);
    await recovered.click();
    await expect(seg(page, "All")).toHaveAttribute("aria-checked", "true");
  });

  test("CL-07 an empty desk and the loading state are designed", async ({ page }) => {
    let release: () => void = () => {};
    const held = new Promise<void>((r) => { release = r; });
    await page.route("**/claims/desk", async (route) => {
      await held;
      await route.fulfill({ json: { now: new Date().toISOString(), rows: [] } });
    });
    await desk(page);
    await expect(page.locator(".cd .kpi-skel").first()).toBeVisible();
    await shot(page, "cl-desktop-loading");
    release();
    await expect(page.getByRole("heading", { name: "No claims yet" })).toBeVisible();
    await expect(page.getByLabel("Summary")).toContainText("Open0none waiting");
    await shot(page, "cl-desktop-empty");
    await page.getByRole("link", { name: "View orders" }).click();
    await expect(page.getByRole("heading", { name: "Delivery Control Room" })).toBeVisible();
  });
});

test.describe("phone, after the claims closed", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("CL-07 a phone shows the closed desk and a paid claim's story", async ({ page }) => {
    await desk(page);
    await expect(page.getByLabel("Claim clock")).toContainText("No open claims");
    const card = page.locator(".ledger-card", { hasText: "Denim jacket" });
    await card.click();
    await expect(story(page, "Denim jacket, size S").getByLabel("History")).toContainText("Paid £40");
    expect(await noOverflow(page)).toBe(true);
    await shot(page, "cl-phone-closed");
  });
});
