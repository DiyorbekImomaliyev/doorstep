// QA-04: a toast shown while a case drawer is open never covers the drawer's action buttons.
import { expect, test, type Page } from "@playwright/test";

type Box = { x: number; y: number; width: number; height: number };
const overlaps = (a: Box, b: Box) => a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height;

async function decideAgain(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "Open my demo shop" }).click();
  const href = (await page.getByRole("link", { name: /Open Malika's Control Room/ }).getAttribute("href"))!;
  await page.goto(href);
  await page.locator(".ag-row", { hasText: "Denim jacket, size S" }).first().locator(".item-name").click();
  const drawer = page.getByRole("dialog", { name: /^Order / });
  await drawer.getByRole("button", { name: "Decide again" }).click();
  const toast = page.locator(".toast").filter({ hasText: "Decided again" });
  await expect(toast).toBeVisible();
  await page.waitForTimeout(300); // past the rise animation
  const t = (await toast.boundingBox())!;
  for (const b of await drawer.locator("footer button").all()) expect(overlaps(t, (await b.boundingBox())!)).toBe(false);
  const d = (await drawer.boundingBox())!;
  expect(t.x).toBeGreaterThanOrEqual(d.x);
  expect(t.x + t.width).toBeLessThanOrEqual(d.x + d.width + 1);
}

test("QA-04 the toast stays clear of the drawer's buttons on a desktop", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await decideAgain(page);
});

test("QA-04 the toast stays clear of the drawer's buttons on a phone", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await decideAgain(page);
});
