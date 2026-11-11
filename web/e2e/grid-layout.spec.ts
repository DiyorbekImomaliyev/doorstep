// GS-01: the grid remembers column widths per browser (filters are not restored).
import { expect, test, type Page } from "@playwright/test";

const width = async (page: Page, col: string) =>
  Math.round((await page.locator(`.orders-grid .ag-header-cell[col-id="${col}"]`).first().boundingBox())!.width);

test("GS-01 a resized column keeps its width after a reload", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Open my demo shop" }).click();
  const href = (await page.getByRole("link", { name: /Open Malika's Control Room/ }).getAttribute("href"))!;
  await page.goto(href);
  const all = page.getByRole("group", { name: "Filter orders" }).getByRole("button", { name: /^All orders/ });
  await all.click();
  const head = page.locator('.orders-grid .ag-header-cell[col-id="buyer"]').first();
  await expect(head).toBeVisible();
  const before = await width(page, "buyer");
  const box = (await head.boundingBox())!;
  await page.mouse.move(box.x + box.width - 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width + 58, box.y + box.height / 2, { steps: 6 });
  await page.mouse.up();
  await expect.poll(() => width(page, "buyer")).toBeGreaterThan(before + 30);
  const after = await width(page, "buyer");

  await page.reload();
  await all.click();
  await expect(head).toBeVisible();
  expect(Math.abs((await width(page, "buyer")) - after)).toBeLessThan(4);
});
