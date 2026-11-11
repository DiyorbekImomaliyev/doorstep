// GP-02: the Control Room's pivot view (AG Grid PivotModule): delivery down, journey across, money per currency.
import { expect, test, type Page } from "@playwright/test";

async function openShop(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "Open my demo shop" }).click();
  const href = (await page.getByRole("link", { name: /Open Malika's Control Room/ }).getAttribute("href"))!;
  await page.goto(href);
  await expect(page.locator(".ag-row .item-name").first()).toBeVisible();
  await page.getByRole("group", { name: "Filter orders" }).getByRole("button", { name: /^All orders/ }).click();
}

test.use({ viewport: { width: 1440, height: 900 } });

const headers = (page: Page) => page.locator(".orders-grid .ag-header-cell-text:visible").allTextContents();

test("GP-02 Pivot shows journeys across and money per currency; off restores the columns", async ({ page }) => {
  await openShop(page);
  const grid = page.locator(".orders-grid");
  const before = await headers(page);
  expect(before).toContain("Journey");

  const pivot = grid.locator(".ag-toolbar").getByRole("button", { name: "Pivot" });
  await pivot.click();
  await expect(pivot).toHaveAttribute("aria-pressed", "true");
  await expect(grid.locator(".ag-row .item-name")).toHaveCount(0);
  // Pivot column groups are the journey labels; each carries an Orders column.
  await expect(grid.locator(".ag-header-group-cell-label").first()).toBeVisible();
  await expect(grid.locator('.ag-header-cell-text:text-is("Orders")').first()).toBeVisible();
  // A grand total row stays.
  await expect(grid.locator(".ag-row-pinned, .ag-row-footer, .ag-row").filter({ hasText: /Total/i }).first()).toBeVisible();
  // Money cells carry their own symbol, and one cell never holds both.
  // The grid draws only the columns in view: scroll across and collect the cells and headers.
  const texts = new Set<string>();
  const heads: string[] = [];
  for (let x = 0; x < 6000; x += 500) {
    await grid.locator(".ag-body-horizontal-scroll-viewport").evaluate((el, left) => { el.scrollLeft = left; }, x);
    await page.waitForTimeout(150);
    (await grid.locator('.ag-cell').allTextContents()).forEach((t) => texts.add(t));
    heads.push(...(await grid.locator(".ag-header-cell-text").allTextContents()));
  }
  const money = [...texts].filter((t) => /[£$]\d/.test(t));
  expect(money.length).toBeGreaterThan(0);
  for (const t of money) expect(/£/.test(t) && /\$/.test(t)).toBe(false);
  // A currency column header exists for every symbol shown in the cells.
  const headText = heads.join("|");
  if (money.some((t) => t.includes("£"))) expect(headText).toContain("At stake £");
  if (money.some((t) => t.includes("$"))) expect(headText).toContain("At stake $");
  await grid.locator(".ag-body-horizontal-scroll-viewport").evaluate((el) => { el.scrollLeft = 0; });

  await pivot.click();
  await expect(pivot).toHaveAttribute("aria-pressed", "false");
  await expect(grid.locator(".ag-row .item-name").first()).toBeVisible();
  expect(await headers(page)).toEqual(before);
});
