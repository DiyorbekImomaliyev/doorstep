// GM-02: the Control Room on a phone is one card per order.
import { expect, test } from "@playwright/test";

test.use({ viewport: { width: 390, height: 844 } });

test("GM-02 at 390 px the grid is one card column: no sideways scroll, the order ref shows", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Open my demo shop" }).click();
  const href = (await page.getByRole("link", { name: /Open Malika's Control Room/ }).getAttribute("href"))!;
  await page.goto(href);
  const card = page.locator(".orders-grid .order-card").first();
  await expect(card).toBeVisible();
  // One column: every visible cell is the card column.
  await expect(page.locator('.orders-grid .ag-center-cols-container .ag-cell:not([col-id="card"])')).toHaveCount(0);
  await expect(card.locator(".oc-meta")).toContainText(/\S+ · \S+/);
  await expect(card.locator("button.row-action")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  // The card fits its row: nothing is cut at the bottom.
  const fits = await card.evaluate((el) => el.scrollHeight <= el.parentElement!.clientHeight + 1);
  expect(fits).toBe(true);
});
