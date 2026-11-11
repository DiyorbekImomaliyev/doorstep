// MN-02..MN-08 in a real browser: the Money ledger on a seeded scratch shop. Every control is
// clicked and its real result checked: KPIs and the weekly flow against the API, status chips,
// the detail panel (lifecycle, copy, Verify receipt), Decide again on a blocked refund followed
// live to Simulated, the currency switch, the Excel export and the phone layout.
import { execFileSync } from "node:child_process";
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const shots = process.env.E2E_SHOTS;
test.describe.configure({ mode: "serial" });
test.use({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: shots ? 2 : 1 });

type Desk = {
  rows: { action_id: string; item: string; kind: string; amount_minor: number; currency: string; status: string; request_id: string | null; receipts: number }[];
  recoveries: { recovered_minor: number; currency: string }[];
};

let token = "";
let seed: { seller_token: string; buyers: Record<string, string> };

const desk = async (request: APIRequestContext) => (await (await request.get(`/api/seller/${token}/money-desk`)).json()) as Desk;
const fmt = (minor: number, currency: string) => new Intl.NumberFormat(currency === "USD" ? "en-US" : "en-GB", {
  style: "currency", currency, maximumFractionDigits: minor % 100 === 0 ? 0 : 2,
}).format(minor / 100);
const refunded = (d: Desk, currency: string) => d.rows
  .filter((r) => ["refund", "partial_refund"].includes(r.kind) && ["completed", "simulated"].includes(r.status) && r.currency === currency)
  .reduce((a, r) => a + r.amount_minor, 0);
const recovered = (d: Desk, currency: string) => d.recoveries.filter((r) => r.currency === currency).reduce((a, r) => a + r.recovered_minor, 0);
const kpi = (page: Page, label: string) =>
  page.getByLabel("Summary").locator(".kpi").filter({ has: page.locator(".label", { hasText: new RegExp(`^${label}$`) }) }).locator(".value");
const chips = (page: Page) => page.getByRole("group", { name: "Filter by status" });
const grid = (page: Page) => page.getByLabel("Money actions");
const dataRows = (page: Page) => grid(page).locator(".ag-row:not(.ag-row-pinned):has(.ag-cell[col-id=status])");

test.beforeAll(async ({ request }) => {
  seed = await (await request.post("/demo/seed?scratch=1")).json();
  token = seed.seller_token;
  // The seeded refunds run in the worker: wait until none is queued and the blocked one is recorded.
  await expect.poll(async () => {
    const d = await desk(request);
    return d.rows.length >= 4 && !d.rows.some((r) => r.status === "queued" || r.status === "pending") && d.rows.some((r) => r.status === "blocked");
  }, { timeout: 30_000 }).toBe(true);
});

async function open(page: Page) {
  await page.goto(`/seller/${token}#money`);
  await expect(page.getByRole("heading", { name: "Money", exact: true })).toBeVisible();
  await expect(dataRows(page).first()).toBeVisible();
}

test("MN-02 MN-03 the KPIs and the weekly flow are the shop's own figures, per currency", async ({ page, request }) => {
  const d = await desk(request);
  await open(page);
  const out = refunded(d, "GBP");
  const back = recovered(d, "GBP");
  await expect(kpi(page, "Refunded")).toHaveText(fmt(out, "GBP"));
  await expect(kpi(page, "Recovered")).toHaveText(fmt(back, "GBP"));
  await expect(kpi(page, "Net cost")).toHaveText(fmt(out - back, "GBP"));
  await expect(kpi(page, "At PayPal")).toHaveText(fmt(0, "GBP"));
  await expect(page.getByLabel("Summary")).toContainText("simulated");
  // The chart draws (AG Charts canvas) and its table view holds this week's money out and back.
  const flow = page.getByLabel("Money flow", { exact: true });
  await expect(flow.locator("canvas").first()).toBeVisible();
  await expect(flow).toContainText("Weekly · GBP");
  const thisWeek = flow.locator("table tbody tr").last();
  await expect(thisWeek).toContainText(fmt(out, "GBP"));
  await expect(thisWeek).toContainText(fmt(back, "GBP"));
  // Proof: one PayPal-Request-Id per write.
  const keyed = d.rows.filter((r) => r.request_id).length;
  await expect(page.getByLabel("Proof")).toContainText(`${keyed}/${keyed}`);
  if (shots) { await page.screenshot({ path: `${shots}/money-desk.png` }); await page.screenshot({ path: `${shots}/money-desk-full.png`, fullPage: true }); }
});

test("MN-04 a status chip filters the grid and its count is live", async ({ page, request }) => {
  const d = await desk(request);
  const simulated = d.rows.filter((r) => r.status === "simulated").length;
  await open(page);
  await expect(chips(page).getByRole("button", { name: /^All/ })).toContainText(String(d.rows.length));
  await expect(chips(page).getByRole("button", { name: /^Simulated/ })).toContainText(String(simulated));
  await expect(chips(page).getByRole("button", { name: /^Failed/ })).toBeDisabled();
  await chips(page).getByRole("button", { name: /^Blocked/ }).click();
  await expect(chips(page).getByRole("button", { name: /^Blocked/ })).toHaveAttribute("aria-pressed", "true");
  await expect(dataRows(page)).toHaveCount(1);
  await expect(dataRows(page).first()).toContainText("Blocked");
  await chips(page).getByRole("button", { name: /^Simulated/ }).click();
  await expect(dataRows(page)).toHaveCount(simulated);
  await chips(page).getByRole("button", { name: /^All/ }).click();
  await expect(dataRows(page)).toHaveCount(d.rows.length);
});

test("MN-05 MN-06 a row opens its lifecycle; copy gives the full Request-Id; Verify receipt shows the signature valid", async ({ page, request, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  const d = await desk(request);
  const sim = d.rows.find((r) => r.status === "simulated" && r.receipts > 0)!;
  await open(page);
  await dataRows(page).filter({ hasText: sim.item }).first().locator(".item-name").click();
  const panel = page.getByRole("region", { name: "Money action details" });
  await expect(panel).toBeVisible();
  const life = panel.getByLabel("Lifecycle", { exact: true });
  for (const step of ["Approved", "Queued in outbox", "Re-checked live", "Refund simulated", "Signed"]) await expect(life).toContainText(step);
  await expect(life).toContainText("5 checks passed");
  await expect(panel.getByLabel("PayPal", { exact: true })).toContainText(sim.request_id!);
  await expect(panel.getByLabel("PayPal", { exact: true }).locator(".checklist li")).toHaveCount(5);
  await panel.getByRole("button", { name: `Copy ${sim.request_id}` }).click();
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe(sim.request_id);
  await panel.getByRole("button", { name: "Verify receipt" }).click();
  await expect(panel.getByText("Signature valid")).toBeVisible();
  await expect(panel).toContainText(/Ed25519 in this browser · chain of \d+ intact/);
  if (shots) { await panel.scrollIntoViewIfNeeded(); await page.screenshot({ path: `${shots}/money-detail-verified.png` }); }
  // The chevron closes it again, from the keyboard too.
  const chevron = dataRows(page).filter({ hasText: sim.item }).first().getByRole("button", { name: /Hide details/ });
  await chevron.focus();
  await page.keyboard.press("Enter");
  await expect(panel).toBeHidden();
});

test("MN-05 MN-08 a blocked refund says why; Decide again queues a new one, followed to Simulated without a reload", async ({ page, request }) => {
  const before = await desk(request);
  const blocked = before.rows.find((r) => r.status === "blocked")!;
  const sims = before.rows.filter((r) => r.status === "simulated").length;
  await open(page);
  await dataRows(page).filter({ hasText: "Blocked" }).first().locator(".item-name").click();
  const panel = page.getByRole("region", { name: "Money action details" });
  await expect(panel).toContainText("Stopped before PayPal");
  await expect(panel).toContainText("No money moved");
  await expect(panel).toContainText(/Failed: \w/);
  await expect(panel.getByLabel("PayPal", { exact: true })).toContainText("Not asked");
  if (shots) { await panel.scrollIntoViewIfNeeded(); await page.screenshot({ path: `${shots}/money-detail-blocked.png` }); }
  const reloads: string[] = [];
  page.on("framenavigated", (f) => { if (f === page.mainFrame()) reloads.push(f.url()); });
  await panel.getByRole("button", { name: "Decide again" }).click();
  await expect(page.getByText("Decided again: a new refund is queued")).toBeVisible();
  // The new refund appears and lands as Simulated on screen, with no page reload.
  await expect(chips(page).getByRole("button", { name: /^Simulated/ })).toContainText(String(sims + 1), { timeout: 20_000 });
  await expect(kpi(page, "Refunded")).toHaveText(fmt(refunded(before, "GBP") + blocked.amount_minor, "GBP"));
  await expect(kpi(page, "At PayPal")).toHaveText(fmt(0, "GBP"));
  await expect(dataRows(page).filter({ hasText: blocked.item })).toHaveCount(2);
  await expect(dataRows(page).filter({ hasText: blocked.item }).filter({ hasText: "Simulated" })).toHaveCount(1);
  expect(reloads).toEqual([]);
  const after = await desk(request);
  const fresh = after.rows.find((r) => r.item === blocked.item && r.status === "simulated")!;
  expect(fresh.request_id).not.toBe(blocked.request_id); // a new decision, a new key; the old one never paid
});

test("MN-03 MN-08 a new USD refund appears live and the currency switch shows its own figures", async ({ page, request }) => {
  await open(page);
  await expect(page.getByRole("group", { name: "Currency" })).toHaveCount(0);
  // A US buyer reports a damaged parcel; the seller approves the held case (existing endpoints).
  const report = await (await request.post(`/api/buyer/${seed.buyers["us-sneakers"]}/not-here`, { data: { reason: "damaged", note: "" } })).json();
  expect((await request.post(`/api/seller/${token}/cases/${report.case_id}/approve`)).ok()).toBe(true);
  const switcher = page.getByRole("group", { name: "Currency" });
  await expect(switcher).toBeVisible({ timeout: 25_000 }); // the page's live poll, no reload
  await expect.poll(async () => (await desk(request)).rows.some((r) => r.currency === "USD" && r.status === "simulated"), { timeout: 20_000 }).toBe(true);
  const d = await desk(request);
  await switcher.getByRole("button", { name: /USD/ }).click();
  await expect(switcher.getByRole("button", { name: /USD/ })).toHaveAttribute("aria-pressed", "true");
  await expect(kpi(page, "Refunded")).toHaveText(fmt(refunded(d, "USD"), "USD"), { timeout: 15_000 });
  await expect(page.getByLabel("Money flow", { exact: true })).toContainText("Weekly · USD");
  await switcher.getByRole("button", { name: /GBP/ }).click();
  await expect(kpi(page, "Refunded")).toHaveText(fmt(refunded(d, "GBP"), "GBP"));
  if (shots) await page.screenshot({ path: `${shots}/money-desk-two-currencies.png` });
});

test("MN-07 Export downloads the whole ledger with totals per currency, never £ plus $", async ({ page, request }) => {
  const d = await desk(request);
  await open(page);
  await chips(page).getByRole("button", { name: /^Blocked/ }).click(); // a filter on screen never shrinks the export
  const [dl] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "Export" }).click()]);
  expect(dl.suggestedFilename()).toMatch(/^doorstep-money-\d{4}-\d{2}-\d{2}\.xlsx$/);
  const file = test.info().outputPath("money.xlsx");
  await dl.saveAs(file);
  const strings = execFileSync("unzip", ["-p", file, "xl/sharedStrings.xml"]).toString();
  expect(strings).toContain("Doorstep · Money ledger");
  for (const t of ["Totals GBP", "Totals USD", "Refunded", "Recovered from carriers", "Net cost", "At PayPal"]) expect(strings).toContain(t);
  for (const r of d.rows) if (r.request_id) expect(strings).toContain(r.request_id);
  expect(strings).not.toMatch(/£[\d.,]+ [+] \$/);
  await expect(page.getByText("Ledger exported")).toBeVisible();
});

test("MN-08 a shop with no money moved gets the designed empty state", async ({ page }) => {
  // A brand-new shop: the two money reads answer empty (stubbed here; the seed always has refunds).
  await page.route(/\/api\/seller\/[^/]+\/money-desk$/, (r) => r.fulfill({ json: { rows: [], recoveries: [], as_of: new Date().toISOString() } }));
  await page.route(/\/api\/seller\/[^/]+\/money$/, (r) => r.fulfill({ json: [] }));
  await page.goto(`/seller/${token}#money`);
  const empty = page.getByLabel("Money actions");
  await expect(empty).toContainText("Nothing moved yet");
  await expect(kpi(page, "Refunded")).toHaveText(fmt(0, "GBP"));
  await expect(page.getByRole("button", { name: "Export" })).toBeDisabled();
  await expect(page.getByLabel("Money flow", { exact: true })).toHaveCount(0);
  if (shots) await page.screenshot({ path: `${shots}/money-empty.png` });
});

test.describe("phone", () => {
  test.use({ viewport: { width: 390, height: 844 } });
  test("MN-08 the ledger fits a phone: KPIs, cards, and a card opens its story", async ({ page }) => {
    await page.goto(`/seller/${token}#money`);
    await expect(page.getByLabel("Summary").locator(".kpi")).toHaveCount(4);
    const card = page.locator(".ledger-card").first();
    await expect(card).toBeVisible();
    await card.click();
    const panel = page.getByRole("region", { name: "Money action details" });
    await expect(panel).toBeVisible();
    await expect(panel.getByRole("button", { name: "Verify receipt" })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
    if (shots) { await page.screenshot({ path: `${shots}/money-phone.png`, fullPage: true }); await panel.scrollIntoViewIfNeeded(); await page.screenshot({ path: `${shots}/money-phone-detail.png` }); }
    await card.click();
    await expect(panel).toBeHidden();
  });
});
