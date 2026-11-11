// GT: the Control Room grid on AG Grid 36 Enterprise's own tools: the Quick Access Toolbar, cell
// selection with a live sum, grouping by any column, and the side bar.
import { expect, test, type Page } from "@playwright/test";

async function openShop(page: Page): Promise<string> {
  await page.goto("/");
  await page.getByRole("button", { name: "Open my demo shop" }).click();
  const sellerHref = (await page.getByRole("link", { name: /Open Malika's Control Room/ }).getAttribute("href"))!;
  await page.goto(sellerHref);
  await expect(page.locator(".ag-row .item-name").first()).toBeVisible();
  return sellerHref;
}

async function allOrders(page: Page) {
  await page.getByRole("group", { name: "Filter orders" }).getByRole("button", { name: /^All orders/ }).click();
}

test("GT-01 the grid's toolbar is AG Grid's: Excel downloads a file, Columns opens the side bar", async ({ page }) => {
  await openShop(page);
  const toolbar = page.locator(".orders-grid .ag-toolbar[role=toolbar]");
  await expect(toolbar).toBeVisible();

  const download = page.waitForEvent("download");
  await toolbar.getByRole("button", { name: "Export to Excel" }).click();
  const file = await download;
  expect(file.suggestedFilename()).toMatch(/^doorstep-orders-\d{4}-\d{2}-\d{2}\.xlsx$/);
  const bytes = await (await import("node:fs/promises")).readFile((await file.path())!);
  expect(bytes.subarray(0, 2).toString()).toBe("PK"); // an .xlsx is a zip

  const sideBar = page.locator(".orders-grid .ag-side-bar");
  await expect(sideBar.locator(".ag-tool-panel-wrapper:visible")).toHaveCount(0);
  await toolbar.getByRole("button", { name: "Columns and filters" }).click();
  await expect(sideBar.locator(".ag-column-select")).toBeVisible();
  await toolbar.getByRole("button", { name: "Columns and filters" }).click();
  await expect(sideBar.locator(".ag-column-select")).toBeHidden();

  await expect(toolbar.getByRole("button", { name: "Download hand-off report" })).toBeVisible();
  await expect(page.getByRole("searchbox", { name: "Search orders" })).toBeVisible();
});

/** "£58" / "$1,089.50" -> { currency, minor }. */
function parseMoney(text: string): { cur: string; minor: number } {
  const t = text.trim();
  return { cur: t.startsWith("$") ? "USD" : "GBP", minor: Math.round(Number(t.replace(/[^0-9.]/g, "")) * 100) };
}
const fmt = (minor: number, cur: string) => new Intl.NumberFormat(cur === "USD" ? "en-US" : "en-GB", {
  style: "currency", currency: cur, maximumFractionDigits: minor % 100 === 0 ? 0 : 2,
}).format(minor / 100);

test("GT-02 dragging across At-stake cells shows AG Grid's live Sum in money; a plain click still opens the order", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await openShop(page);
  await allOrders(page);
  const cells = page.locator('.orders-grid .ag-row .ag-cell[col-id="money"]');
  await expect(cells.nth(2)).toBeVisible();
  await page.waitForTimeout(600); // rows settle into the work order (animated)
  // The first three At-stake cells as the seller sees them, top to bottom (the DOM order may differ).
  const seen = await Promise.all((await cells.all()).map(async (c) => ({ c, y: (await c.boundingBox())!.y, t: await c.locator(".amt").innerText() })));
  const top = seen.sort((x, y) => x.y - y.y).slice(0, 3);
  const by: Record<string, number> = {};
  for (const { t } of top) { const m = parseMoney(t); by[m.cur] = (by[m.cur] ?? 0) + m.minor; }
  const expected = Object.entries(by).map(([c, v]) => fmt(v, c)).join(" + ");

  const a = (await top[0].c.boundingBox())!, b = (await top[2].c.boundingBox())!;
  await page.mouse.move(a.x + a.width / 2, a.y + a.height / 2);
  await page.mouse.down();
  await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2, { steps: 8 });
  await page.mouse.up();

  const status = page.locator(".orders-grid .ag-status-bar .ag-status-panel-aggregations");
  await expect(status.locator(".ag-status-name-value", { hasText: "Sum" })).toContainText(expected);
  await expect(status.locator(".ag-status-name-value", { hasText: "Count" })).toContainText("3");
  await expect(page.getByRole("dialog", { name: /^Order / })).toHaveCount(0); // the drag selected, it did not open

  // Ctrl/Cmd+C copies the selection as plain numbers a spreadsheet can add.
  await page.keyboard.press("ControlOrMeta+c");
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toMatch(/^\d+\.\d{2}\r?\n\d+\.\d{2}\r?\n\d+\.\d{2}\s*$/);

  // One plain click on a row still opens that order.
  await page.locator('.orders-grid .ag-row .ag-cell[col-id="status"]').first().click();
  await expect(page.getByRole("dialog", { name: /^Order / })).toBeVisible();
});

async function headerMenu(page: Page, colId: string) {
  const head = page.locator(`.orders-grid .ag-header-cell[col-id="${colId}"]`);
  await head.hover();
  await head.locator(".ag-header-cell-menu-button").click();
}

test("GT-03 Group by Delivery from the header menu: group rows count every order and sum the money; the pill ungroups", async ({ page }) => {
  await openShop(page);
  await allOrders(page);
  const total = Number((await page.locator(".table-card .head .count-chip").innerText()).trim());
  const toolbar = page.locator(".orders-grid .ag-toolbar");
  await expect(toolbar.locator(".ag-column-drop-cell", { hasText: "Lane" })).toBeVisible(); // lanes are the default grouping

  await headerMenu(page, "method");
  await page.getByRole("menuitem", { name: "Group by Delivery" }).click();

  const pill = toolbar.locator(".ag-column-drop-cell", { hasText: "Delivery" });
  await expect(pill).toBeVisible();
  const groups = page.locator(".orders-grid .lane-row.group-by");
  await expect(groups.first()).toBeVisible();
  await expect(groups.first()).toHaveAttribute("aria-label", /^(Home|Locker|Signature|Not chosen)[^:]*: \d+ orders?$/);
  await expect(groups.first().locator(".lane-meta")).toHaveText(/^[£$][\d,.]+( \+ [£$][\d,.]+)? at stake$/);
  const counts = await groups.locator(".count-chip").allInnerTexts();
  expect(counts.reduce((a, c) => a + Number(c), 0)).toBe(total); // every order sits in exactly one group

  await pill.locator(".ag-column-drop-cell-button").click();
  await expect(groups).toHaveCount(0);
  await expect(toolbar.locator(".ag-column-drop-cell", { hasText: "Lane" })).toBeVisible();
});

test("GT-04 At stake shows values as % of total, per currency, from its header menu", async ({ page }) => {
  await openShop(page);
  await allOrders(page);
  await headerMenu(page, "money");
  await page.getByRole("menuitem", { name: /Show Values As/i }).hover();
  await page.locator(".ag-menu-option", { hasText: "% of total" }).click();

  const amts = page.locator('.orders-grid .ag-row .ag-cell[col-id="money"] .amt');
  await expect(amts.first()).toHaveText(/^\d{1,3}\.\d%$/);
  const subs = await page.locator('.orders-grid .ag-row .ag-cell[col-id="money"]').evaluateAll((cells) =>
    cells.map((c) => ({ pct: c.querySelector(".amt")?.textContent ?? "", money: c.querySelector(".sub")?.textContent ?? "" })));
  // Each currency's shares add up to 100% (a pound is never a share of dollars).
  const byCur: Record<string, number> = {};
  for (const s of subs) byCur[s.money.trim().startsWith("$") ? "USD" : "GBP"] = (byCur[s.money.trim().startsWith("$") ? "USD" : "GBP"] ?? 0) + parseFloat(s.pct);
  for (const v of Object.values(byCur)) expect(Math.abs(v - 100)).toBeLessThan(1);
});

test("GT-05 the grand total row sums money at stake per currency and follows the search", async ({ page }) => {
  await openShop(page);
  await allOrders(page);
  const total = page.locator(".orders-grid .total-row");
  await expect(total).toHaveCount(1); // the old row glides out when the lanes come in
  const count = page.locator(".table-card .head .count-chip");
  await expect(total).toHaveAttribute("aria-label", new RegExp(`^Total: ${(await count.innerText()).trim()} orders, `));
  // The lanes' sums, per currency, add up to the total.
  const by: Record<string, number> = {};
  for (const t of await page.locator(".orders-grid .lane-row .lane-meta").allInnerTexts()) {
    for (const part of t.replace(" at stake", "").split(" + ")) { const m = parseMoney(part); by[m.cur] = (by[m.cur] ?? 0) + m.minor; }
  }
  const expected = Object.entries(by).filter(([, v]) => v > 0).map(([c, v]) => fmt(v, c));
  for (const e of expected) await expect(total.locator(".total-stake")).toContainText(e);

  await page.getByRole("searchbox", { name: "Search orders" }).fill("jacket");
  await expect(page.locator(".ag-status-bar")).toContainText(/\d+ of \d+/);
  const shown = (await page.locator(".ag-status-bar").innerText()).match(/(\d+) of \d+/)![1];
  await expect(total).toHaveCount(1);
  await expect(total).toHaveAttribute("aria-label", new RegExp(`^Total: ${shown} orders?, `));
});

test("GT-06 the side bar: Ask's filters show in AG Grid's New Filters panel; the Doorstep panel counts lanes and clears them", async ({ page }) => {
  await openShop(page);
  await allOrders(page);
  await page.route("**/grid/ask", (route) => route.fulfill({
    json: {
      question: "lost parcels", mode: "keywords", provider: null,
      plan: { answerable: true, view: "all", journey: ["Lost by carrier"], next_step: [], delivery: [], min_at_stake: null, max_proof: null, sort: [], show: [], note: "Read by keywords." },
    },
  }));
  const box = page.getByRole("searchbox", { name: "Search orders" });
  await box.fill("lost parcels");
  await box.press("Enter");
  const status = page.locator(".orders-grid .ag-status-bar");
  await expect(status).toContainText(/\d+ of \d+/);
  const shown = Number((await status.innerText()).match(/(\d+) of \d+/)![1]);

  await page.locator(".orders-grid .ag-toolbar").getByRole("button", { name: "Columns and filters" }).click();
  const side = page.locator(".orders-grid .ag-side-bar");
  await side.locator(".ag-side-button", { hasText: "Filters" }).click();
  await expect(side.locator(".ag-tool-panel-wrapper:visible")).toContainText("Journey");

  await side.locator(".ag-side-button", { hasText: "Doorstep" }).click();
  const panel = side.locator(".ds-panel");
  await expect(panel).toBeVisible();
  const counts = (await panel.locator(".ds-panel-lanes dd").allInnerTexts()).map(Number);
  expect(counts).toHaveLength(4);
  expect(counts.reduce((a, b) => a + b, 0)).toBe(shown);
  await expect(panel.locator(".ds-panel-stake strong")).toHaveText(/^[£$][\d,.]+( \+ [£$][\d,.]+)?$/);

  const clear = panel.getByRole("button", { name: "Clear filters" });
  await expect(clear).toBeEnabled();
  await clear.click();
  await expect(status).not.toContainText(/\d+ of \d+/);
  await expect(clear).toBeDisabled();
  expect((await panel.locator(".ds-panel-lanes dd").allInnerTexts()).map(Number).reduce((a, b) => a + b, 0))
    .toBe(Number((await page.locator(".table-card .head .count-chip").innerText()).trim()));
});
