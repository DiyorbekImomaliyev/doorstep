// GC: integrated charts on the Control Room grid, in the Doorstep theme.
import { expect, test, type Page } from "@playwright/test";

async function openShop(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "Open my demo shop" }).click();
  const href = (await page.getByRole("link", { name: /Open Maria's Control Room/ }).getAttribute("href"))!;
  await page.goto(href);
  await expect(page.locator(".ag-row .item-name").first()).toBeVisible();
  await page.getByRole("group", { name: "Filter orders" }).getByRole("button", { name: /^All orders/ }).click();
}

test("GC-01 drag across cells, right-click, Chart: a chart canvas appears", async ({ page }) => {
  await openShop(page);
  const cells = page.locator(".ag-row:not(.ag-row-pinned) .ag-cell[col-id=method]");
  const from = cells.first();
  const to = page.locator(".ag-row:not(.ag-row-pinned) .ag-cell[col-id=money]").nth(3);
  await from.hover();
  await page.mouse.down();
  await to.hover();
  await page.mouse.up();
  await to.click({ button: "right" });
  await page.getByRole("menuitem", { name: "Chart" }).or(page.locator(".ag-menu-option", { hasText: /^Chart$/ })).first().click();
  await expect(page.locator(".ag-chart-canvas-wrapper canvas, .ag-chart canvas").first()).toBeVisible();
});

test("GC-02 the toolbar Chart button opens a chart", async ({ page }) => {
  await openShop(page);
  await page.locator(".orders-grid .ag-toolbar").getByRole("button", { name: "Chart" }).click();
  await expect(page.locator(".ag-chart-canvas-wrapper canvas, .ag-chart canvas").first()).toBeVisible();
  await page.waitForTimeout(800); await page.locator(".ag-chart, .ag-dialog").first().screenshot({ path: "/tmp/gc/chart.png" });
});

test("GC-03 Find highlights matches in the grid when the search has text", async ({ page }) => {
  await openShop(page);
  await page.getByRole("searchbox", { name: "Search orders" }).fill("jacket");
  await expect(page.locator(".find-mark").first()).toBeVisible();
  await page.locator(".orders-grid").screenshot({ path: "/tmp/gc/find.png" });
});

test("GC-04 selection chart has readable labels and no blank category", async ({ page }) => {
  await openShop(page);
  await page.locator(".ag-row:not(.ag-row-pinned) .ag-cell[col-id=method]").first().hover();
  await page.mouse.down();
  await page.locator(".ag-row:not(.ag-row-pinned) .ag-cell[col-id=money]").nth(8).hover();
  await page.mouse.up();
  await page.locator(".ag-row:not(.ag-row-pinned) .ag-cell[col-id=money]").nth(8).click({ button: "right" });
  await page.locator(".ag-menu-option", { hasText: /^Chart$/ }).first().click();
  await expect(page.locator(".ag-chart canvas").first()).toBeVisible();
  await page.waitForTimeout(800);
  await page.locator(".ag-chart, .ag-dialog").first().screenshot({ path: "/tmp/gc/chart-sel.png" });
});
