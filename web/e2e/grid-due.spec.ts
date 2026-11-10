// GU: the Due column (carrier claim countdown) and the packing list PDF in the Control Room grid.
import { expect, test, type Page } from "@playwright/test";

async function openShop(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "Open my demo shop" }).click();
  const href = (await page.getByRole("link", { name: /Open Maria's Control Room/ }).getAttribute("href"))!;
  await page.goto(href);
  await expect(page.locator(".ag-row .item-name").first()).toBeVisible();
  await page.getByRole("group", { name: "Filter orders" }).getByRole("button", { name: /^All orders/ }).click();
}

test("GU-01 the Due column shows a day count for an order with an open claim", async ({ page }) => {
  await openShop(page);
  await expect(page.locator('.orders-grid .ag-header-cell[col-id="due"]')).toBeVisible();
  const cells = page.locator('.orders-grid .ag-row .ag-cell[col-id="due"] .due-cell');
  // The demo seed prepares carrier claims for several cases; the cell reads "12d" (or "Today").
  await expect(cells.first()).toHaveText(/^(\d+d|Today)$/);
  expect(await cells.count()).toBeGreaterThan(0);
  // Sorting by Due puts the soonest first.
  await page.locator('.orders-grid .ag-header-cell[col-id="due"]').click();
  const first = await page.locator('.orders-grid .ag-row .ag-cell[col-id="due"] .due-cell').first().innerText();
  expect(first).toMatch(/^(\d+d|Today)$/);
});

test("GU-02 Packing list downloads a PDF, or says Nothing to pack", async ({ page }) => {
  await openShop(page);
  const button = page.locator(".orders-grid .ag-toolbar[role=toolbar]").getByRole("button", { name: "Packing list" });
  await expect(button).toBeVisible();
  const download = page.waitForEvent("download", { timeout: 6000 }).catch(() => null);
  await button.click();
  const file = await download;
  if (file) {
    expect(file.suggestedFilename()).toMatch(/\.pdf$/);
    const bytes = await (await import("node:fs/promises")).readFile((await file.path())!);
    expect(bytes.subarray(0, 4).toString()).toBe("%PDF");
  } else {
    await expect(page.getByRole("status").filter({ hasText: "Nothing to pack" })).toBeVisible();
  }
});
