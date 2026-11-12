// UI-01..: the final design pass in a real browser, on one seeded scratch shop.
import { expect, test, type Page } from "@playwright/test";

let seed: { seller_token: string; buyers: Record<string, string>; summary: { seller_id: string; orders: { key: string; case_id: string | null }[] } };
test.beforeAll(async ({ request }) => {
  seed = await (await request.post("/demo/seed?scratch=1")).json();
});

const noOverflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);

/** Space between the grid's last row and its status bar (0 when the grid fits its rows). */
const gridGap = (page: Page) => page.evaluate(() => {
  const g = document.querySelector(".orders-grid")!;
  const rows = [...g.querySelectorAll(".ag-grid-scrollable-area .ag-row")].filter((x) => x.getBoundingClientRect().height > 0 && !x.closest(".ag-details-row"));
  const bottom = Math.max(...rows.map((x) => x.getBoundingClientRect().bottom));
  return Math.round(g.querySelector(".ag-status-bar")!.getBoundingClientRect().top - bottom);
});

test("UI-01 the Control Room grid is as tall as its rows, with lanes open or closed", async ({ page }) => {
  await page.goto(`/seller/${seed.seller_token}`);
  await page.locator("aside.sidebar").getByRole("button", { name: /^All orders/ }).click();
  const lanes = page.locator(".orders-grid .lane-row");
  await expect(lanes).toHaveCount(3);
  await expect.poll(() => gridGap(page)).toBe(0);
  for (let i = 0; i < 3; i++) await lanes.nth(i).click();
  await expect(page.locator(".orders-grid .ag-row-pinned, .orders-grid .ag-row-footer").last()).toBeVisible();
  await expect.poll(() => gridGap(page)).toBe(0);
  for (let i = 0; i < 3; i++) await lanes.nth(i).click();
  await expect.poll(() => gridGap(page)).toBe(0);
  // A search that matches nothing keeps room for the empty state.
  await page.getByRole("searchbox", { name: "Search orders" }).fill("zzzz");
  await expect(page.getByText("No match.")).toBeVisible();
});


