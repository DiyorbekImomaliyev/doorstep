// GT: the Control Room grid on AG Grid 36 Enterprise's own tools: the Quick Access Toolbar, cell
// selection with a live sum, grouping by any column, and the side bar.
import { expect, test, type Page } from "@playwright/test";

async function openShop(page: Page): Promise<string> {
  await page.goto("/");
  await page.getByRole("button", { name: "Open my demo shop" }).click();
  const sellerHref = (await page.getByRole("link", { name: /Open Maria's Control Room/ }).getAttribute("href"))!;
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
