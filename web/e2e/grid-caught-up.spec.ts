// GR-04: an empty Needs you list is calm: a check, "All caught up", and two links.
import { expect, test } from "@playwright/test";

test("GR-04 an empty Needs you list says All caught up, with Claims and Insights links", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Open my demo shop" }).click();
  const href = (await page.getByRole("link", { name: /Open Malika's Control Room/ }).getAttribute("href"))!;
  await page.route(/\/api\/seller\/[^/]+\/orders(\?.*)?$/, (route) =>
    route.request().method() === "GET" ? route.fulfill({ json: [] }) : route.continue());
  await page.goto(href);
  // With nothing to do the Control Room opens on All orders; the seller picks Needs you.
  await page.getByRole("group", { name: "Filter orders" }).getByRole("button", { name: /^Needs you/ }).click();
  const empty = page.locator(".orders-grid .caught-up");
  await expect(empty.getByText("All caught up", { exact: true })).toBeVisible();
  await expect(empty.locator("svg")).toBeVisible();
  await expect(empty.getByRole("link", { name: "Claims" })).toHaveAttribute("href", "#claims");
  await expect(empty.getByRole("link", { name: "Insights" })).toHaveAttribute("href", "#insights");
  await page.screenshot({ path: process.env.SHOT_DIR ? `${process.env.SHOT_DIR}/caught-up.png` : test.info().outputPath("caught-up.png") });
});
