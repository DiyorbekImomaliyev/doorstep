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
