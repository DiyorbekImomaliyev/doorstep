// FP-W: the final polish pass on the seller workspace.
import { expect, test, type Page } from "@playwright/test";

async function openShop(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "Open my demo shop" }).click();
  const href = (await page.getByRole("link", { name: /Open Malika's Control Room/ }).getAttribute("href"))!;
  await page.goto(href);
  await expect(page.locator(".ag-row .item-name").first()).toBeVisible();
}

test("FP-W1 a case with a carrier claim shows its claim tag, and the tag opens the Claims page", async ({ page }) => {
  await openShop(page);
  // Needs you: the lost denim jacket's case has a £75 claim ready to file.
  const row = page.locator(".orders-grid .ag-row", { hasText: "Denim jacket, size S" }).first();
  const tag = row.getByRole("button", { name: /^Carrier claim £75(\.00)?, Ready\. Open Claims$/ });
  await expect(tag).toBeVisible();
  await expect(tag).toHaveText(/£75(\.00)?\s*Ready/);
  // The days left to file ride under it.
  await expect(row.locator(".due-cell")).toHaveText(/^(\d+d|Today)$/);

  // Cases: a settled case keeps its claim, status-aware (the leather belt's carrier paid).
  await page.getByRole("group", { name: "Filter orders" }).getByRole("button", { name: /^Cases/ }).click();
  const belt = page.locator(".orders-grid .ag-row", { hasText: "Leather belt, brown" }).first();
  await expect(belt.getByRole("button", { name: /^Carrier claim .+, Paid\. Open Claims$/ })).toBeVisible();

  // Clicking the tag opens the Claims page, not the order.
  await page.getByRole("group", { name: "Filter orders" }).getByRole("button", { name: /^Needs you/ }).click();
  await tag.click();
  await expect(page.getByRole("heading", { level: 1, name: "Claims" })).toBeVisible();
  // ...focused on that claim (the Claims page reads ?claim=<recovery id>).
  await expect(page).toHaveURL(/\?claim=[0-9a-f-]+#claims$/);
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByRole("group", { name: "Resolve" }).getByRole("button", { name: /^Claims/ })).toHaveAttribute("aria-pressed", "true");
});

test("FP-W1 on a phone the order card carries the claim tag, and it opens Claims", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openShop(page);
  const card = page.locator(".order-card", { hasText: "Denim jacket, size S" });
  const tag = card.getByRole("button", { name: /^Carrier claim £75(\.00)?, Ready\. Open Claims$/ });
  await expect(tag).toHaveText(/Claim £75(\.00)?\s*·?\s*Ready/);
  await tag.click();
  await expect(page.getByRole("heading", { level: 1, name: "Claims" })).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(0);
});
