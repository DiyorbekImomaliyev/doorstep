// GD-01: an order with a case opens a nested timeline grid inside its detail panel.
import { expect, test } from "@playwright/test";

test("GD-01 the detail panel of an order with a case shows a timeline grid with a time and an event", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Open my demo shop" }).click();
  const href = (await page.getByRole("link", { name: /Open Maria's Control Room/ }).getAttribute("href"))!;
  await page.goto(href);
  await page.getByRole("group", { name: "Filter orders" }).getByRole("button", { name: /^All orders/ }).click();

  const expanders = page.locator(".orders-grid .expander");
  await expect(expanders.first()).toBeVisible();
  const n = await expanders.count();
  let found = false;
  for (let i = 0; i < n && !found; i++) {
    await expanders.nth(i).click();
    try {
      await page.locator(".detail-timeline .timeline-grid").first().waitFor({ state: "visible", timeout: 2500 });
      found = true;
    } catch {
      await expanders.nth(i).click();
    }
  }
  expect(found).toBe(true);
  const grid = page.locator(".detail-timeline .timeline-grid").first();
  await expect(grid.getByRole("columnheader", { name: "When" })).toBeVisible();
  const first = grid.locator(".ag-center-cols-container [role=row]").first();
  await expect(first).toBeVisible();
  await expect(first.locator("[col-id=at]")).toContainText(/ago|just now/);
  await expect(first.locator("[col-id=title]")).not.toBeEmpty();
});
