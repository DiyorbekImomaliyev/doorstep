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
  const head = page.locator('.orders-grid .ag-header-cell[col-id="method"]').first();
  await expect(head).toBeVisible();
  const before = await width(page, "method");
  // Drag AG Grid's own resize handle on the header's right edge.
  await head.hover();
  const grip = (await head.locator(".ag-header-cell-resize").boundingBox())!;
  const [x, y] = [grip.x + grip.width / 2, grip.y + grip.height / 2];
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x + 60, y, { steps: 8 });
  await page.mouse.up();
  await expect.poll(() => width(page, "method")).toBeGreaterThan(before + 30);
  const after = await width(page, "method");

  await page.reload();
  await all.click();
  await expect(head).toBeVisible();
  expect(Math.abs((await width(page, "method")) - after)).toBeLessThan(4);
});
