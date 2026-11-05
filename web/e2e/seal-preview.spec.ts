// SL-07 in a real browser: opened from the seller's own browser, the seal page is a preview.
// Taps are recorded, never count as proof, and the order does not change.
import { expect, test } from "@playwright/test";

test("SL-07 the seller's own browser sees a preview, and the order stays as it was", async ({ page, context }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Open my demo shop" }).click();
  const seller = await page.getByRole("link", { name: /Open Maria's Control Room/ }).getAttribute("href");
  await page.goto(seller!);
  await page.getByRole("button", { name: /^All/ }).click();
  await page.getByText("Brass desk lamp").click();
  const seal = page.getByLabel("Possession seal");
  await seal.getByRole("button", { name: "Show seal" }).click();
  const href = await seal.getByRole("link", { name: /Open seal page/ }).getAttribute("href");

  const same = await context.newPage();  // same browser as the Control Room
  await same.goto(new URL(href!).pathname);
  await expect(same.getByLabel("Seller preview")).toBeVisible();
  await same.getByRole("button", { name: "I have it" }).click();
  await expect(same.getByText("Preview only")).toBeVisible();
  await expect(same.getByText("The seller knows you have it")).toHaveCount(0);

  await page.getByRole("button", { name: "Close" }).click();
  await page.getByRole("button", { name: "Refresh" }).click();
  const row = page.locator(".ag-row", { hasText: "Brass desk lamp" });
  await expect(row).not.toContainText("Received");
});
