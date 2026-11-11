// DS-02..DS-07 in a real browser: the PayPal disputes desk on a seeded scratch shop. Every control is
// clicked and its real result checked; the evidence goes out through the case drawer's own flow.
import { expect, test, type Page } from "@playwright/test";

const shots = process.env.E2E_SHOTS;

// One private demo shop for the file (the seed is rate-limited). Tests run in order: the desktop
// test sends the demo dispute's evidence, the phone test then reads the sent state.
let token = "";
test.beforeAll(async ({ request }) => {
  const seed = await (await request.post("/demo/seed?scratch=1")).json();
  token = seed.seller_token as string;
  await expect.poll(async () => (await (await request.get(`/api/seller/${token}/disputes/desk`)).json()).length, { timeout: 30_000 }).toBe(1);
});

function watchErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push(String(e)));
  return errors;
}

const noOverflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);

test.describe("desktop", () => {
  test.use({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 });

  test("DS-02 DS-03 DS-04 DS-05 DS-06 the desk: KPIs, filters, detail, and evidence sent through the drawer", async ({ page, context }) => {
    test.setTimeout(120_000);
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    const errors = watchErrors(page);
    await page.goto(`/seller/${token}#disputes`);
    await expect(page.getByRole("heading", { name: "Disputes" })).toBeVisible();

    // DS-02 KPI strip from the stored rows.
    const kpis = page.getByLabel("Summary");
    await expect(kpis).toContainText("Open1");
    await expect(kpis).toContainText("1 needs a reply");
    await expect(kpis).toContainText("$89");
    await expect(kpis).toContainText("none resolved yet");
    await expect(kpis).toContainText("0 of 1");

    // DS-03 segmented filter with live counts.
    const segs = page.getByRole("group", { name: "Filter disputes" });
    await expect(segs.getByRole("button", { name: "All 1" })).toHaveAttribute("aria-pressed", "true");
    await expect(segs.getByRole("button", { name: "Needs reply 1" })).toBeVisible();
    await expect(segs.getByRole("button", { name: "Evidence sent 0" })).toBeVisible();

    const grid = page.getByLabel("PayPal disputes");
    const row = grid.locator(".ag-row:not(.ag-full-width-row)").first();
    await expect(row).toContainText("Wireless headphones");
    await expect(grid).toContainText("SIM-DISPUTE");
    await expect(row).toContainText("Needs reply");
    await expect(row).toContainText("2/5");

    // DS-05 the detail is open for the dispute that needs a reply.
    const detail = grid.locator(".dd-detail");
    await expect(detail).toBeVisible();
    await expect(detail.getByLabel("Seller Protection")).toContainText("Delivered scan");
    await expect(detail.getByLabel("Seller Protection")).toContainText("2 of 5 met");
    await expect(detail.getByLabel("Evidence sent")).toContainText("Nothing sent yet");
    await expect(detail.getByLabel("At PayPal")).toContainText("Demo · simulated");
    await expect(detail.getByLabel("At PayPal")).not.toContainText("Reply by"); // never stored for the demo
    if (shots) { await page.waitForTimeout(400); await page.screenshot({ path: `${shots}/ds-desktop-needs-reply.png`, fullPage: true }); }

    // Filters: an empty one says so and goes back; Needs reply keeps the row.
    await segs.getByRole("button", { name: "Resolved 0" }).click();
    await expect(page.getByText("Nothing in Resolved")).toBeVisible();
    await page.getByRole("button", { name: "Show all" }).click();
    await expect(segs.getByRole("button", { name: "All 1" })).toHaveAttribute("aria-pressed", "true");
    await segs.getByRole("button", { name: "Needs reply 1" }).click();
    await expect(segs.getByRole("button", { name: "Needs reply 1" })).toHaveAttribute("aria-pressed", "true");
    await expect(row).toContainText("Wireless headphones");
    await segs.getByRole("button", { name: "All 1" }).click();

    // A row click folds and unfolds the detail.
    await row.locator('[col-id="reason"]').click();
    await expect(detail).toHaveCount(0);
    await row.locator('[col-id="reason"]').click();
    await expect(detail).toBeVisible();

    // Keyboard: Enter on a focused cell folds and unfolds the detail too.
    await row.locator('[col-id="reason"]').focus();
    await page.keyboard.press("Enter");
    await expect(detail).toHaveCount(0);
    await page.keyboard.press("Enter");
    await expect(detail).toBeVisible();

    // The PayPal id copies.
    await row.locator('[col-id="paypal_dispute_id"] .copy-id').click();
    expect(await page.evaluate(() => navigator.clipboard.readText())).toMatch(/^SIM-DISPUTE-/);

    // Evidence used folds open.
    await detail.getByText("Evidence used (3)").click();
    await expect(detail).toContainText("Carrier scanned the parcel as delivered");

    // Refresh reads the desk again.
    const again = page.waitForResponse((r) => r.url().includes("/disputes/desk") && r.status() === 200);
    await page.getByRole("button", { name: "Refresh disputes" }).click();
    await again;

    // DS-06 one click: the row's Send evidence opens the case drawer, whose policy-gated flow sends it.
    await row.getByRole("button", { name: "Send evidence" }).click();
    const drawer = page.getByRole("dialog");
    await expect(drawer).toBeVisible();
    await drawer.getByRole("button", { name: "Send evidence to PayPal" }).click();
    await expect(page.locator(".toast")).toContainText("Evidence is on its way");
    await page.keyboard.press("Escape");
    await expect(drawer).toHaveCount(0);

    // The worker sends it; the desk follows on its own.
    await expect(row).toContainText("Evidence sent", { timeout: 30_000 });
    await expect(row).toContainText("3/5");
    await expect(segs.getByRole("button", { name: "Evidence sent 1" })).toBeVisible();
    await expect(segs.getByRole("button", { name: "Needs reply 0" })).toBeVisible();
    await expect(kpis).toContainText("none need you");
    await expect(page.locator(".nav button", { hasText: "Disputes" }).locator(".count")).toHaveText("1"); // still open at PayPal
    const sent = detail.getByLabel("Evidence sent");
    await expect(sent).toContainText("Proof of fulfilment");
    await expect(sent).toContainText("Simulated");
    await expect(sent).toContainText("Request ID");
    await expect(sent.locator(".copy-id")).toHaveAttribute("title", /\S+ · click to copy/);
    await expect(detail.getByLabel("Seller Protection")).toContainText("3 of 5 met");
    const timeline = detail.getByLabel("Dispute timeline");
    const more = timeline.getByRole("button", { name: /Show all/ });
    if (await more.count()) await more.click();
    await expect(timeline).toContainText("Evidence queued");
    await expect(timeline).toContainText("Evidence simulated");
    await expect(timeline).toContainText("Signed receipt");

    // The notes PayPal received, word for word.
    await sent.getByText(/Notes sent/).click();
    await expect(sent).toContainText("Doorstep evidence for order");

    // The signed receipts open on the public verify page.
    const receipt = sent.getByRole("link", { name: /signed receipt/ });
    await expect(receipt).toHaveAttribute("href", /^\/verify\//);
    const [verify] = await Promise.all([page.waitForEvent("popup"), receipt.click()]);
    await expect(verify).toHaveURL(/\/verify\//);
    await verify.close();

    // Open case, from the detail and from the row, shows the evidence as sent in the drawer.
    await sent.getByRole("button", { name: "Open case" }).click();
    await expect(drawer).toContainText("Evidence sent to PayPal");
    await page.keyboard.press("Escape");
    await row.getByRole("button", { name: "Open case" }).click();
    await expect(drawer).toBeVisible();
    await page.keyboard.press("Escape");
    if (shots) { await page.waitForTimeout(400); await page.screenshot({ path: `${shots}/ds-desktop-evidence-sent.png`, fullPage: true }); }
    expect(errors).toEqual([]);
  });

  test("DS-07 empty and loading states are designed", async ({ page }) => {
    const errors = watchErrors(page);
    let release: () => void = () => {};
    const held = new Promise<void>((r) => { release = r; });
    await page.route("**/disputes/desk", async (route) => { await held; await route.fulfill({ json: [] }); });
    await page.goto(`/seller/${token}#disputes`);
    await expect(page.getByLabel("Loading disputes")).toBeVisible();
    if (shots) await page.screenshot({ path: `${shots}/ds-desktop-loading.png` });
    release();
    await expect(page.getByLabel("No disputes")).toContainText("No disputes");
    await expect(page.getByLabel("Summary")).toContainText("no open disputes");
    if (shots) await page.screenshot({ path: `${shots}/ds-desktop-empty.png` });
    expect(errors).toEqual([]);
  });
});

test.describe("phone", () => {
  test.use({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2 });

  test("DS-07 the desk on a phone: summary, filters, cards with their detail, no sideways scroll", async ({ page }) => {
    const errors = watchErrors(page);
    await page.goto(`/seller/${token}#disputes`);
    await expect(page.getByLabel("Summary")).toBeVisible();
    const card = page.locator(".ledger-card").first();
    await expect(card).toContainText("Wireless headphones");
    await expect(card).toContainText("Evidence sent");
    expect(await noOverflow(page)).toBe(true);
    const segs = page.getByRole("group", { name: "Filter disputes" });
    await segs.getByRole("button", { name: "Evidence sent 1" }).click();
    await expect(card).toBeVisible();
    await segs.getByRole("button", { name: "Needs reply 0" }).click();
    await expect(page.getByText("Nothing in Needs reply")).toBeVisible();
    await page.getByRole("button", { name: "Show all" }).click();
    await card.getByRole("button", { name: "Details" }).click();
    const detail = page.locator(".dd-cards .dd-detail");
    await expect(detail).toContainText("Proof of fulfilment");
    expect(await noOverflow(page)).toBe(true);
    if (shots) await page.screenshot({ path: `${shots}/ds-phone.png`, fullPage: true });
    await card.getByRole("button", { name: "Hide" }).click();
    await expect(detail).toHaveCount(0);
    await card.getByRole("button", { name: "Open case" }).click();
    await expect(page.getByRole("dialog")).toContainText("Evidence sent to PayPal");
    await page.keyboard.press("Escape");
    expect(errors).toEqual([]);
  });
});
