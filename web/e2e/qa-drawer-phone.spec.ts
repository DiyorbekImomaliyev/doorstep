// QA-05: on a phone every action in the case drawer is on screen, the main one first and full width.
import { expect, test } from "@playwright/test";

test("QA-05 the drawer's actions fit a 390 px phone, main action first", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.getByRole("button", { name: "Open my demo shop" }).click();
  const href = (await page.getByRole("link", { name: /Open Malika's Control Room/ }).getAttribute("href"))!;
  await page.goto(href);
  const drawer = page.getByRole("dialog", { name: /^Order / });

  for (const [item, main] of [["Denim jacket, size S", /^Approve refund/], ["Wireless headphones", /Send evidence to PayPal|Offer .* refund in the dispute/]] as const) {
    await page.locator(".ag-row", { hasText: item }).first().locator(".item-name").click();
    const buttons = drawer.locator("footer button");
    await expect(buttons.first()).toBeVisible();
    await expect.poll(async () => Math.round((await drawer.boundingBox())!.x)).toBe(0); // slid fully in
    for (const b of await buttons.all()) {
      const box = (await b.boundingBox())!;
      expect(box.x).toBeGreaterThanOrEqual(0);
      expect(box.x + box.width).toBeLessThanOrEqual(390);
    }
    // The main action is the top row, alone and full width.
    const primary = drawer.locator("footer button.lg");
    await expect(primary).toHaveText(main);
    const p = (await primary.boundingBox())!;
    const tops = await Promise.all((await buttons.all()).map(async (b) => (await b.boundingBox())!.y));
    expect(p.y).toBeLessThanOrEqual(Math.min(...tops) + 1);
    expect(p.width).toBeGreaterThan(300);
    await drawer.getByRole("button", { name: "Close", exact: true }).click();
  }
});
