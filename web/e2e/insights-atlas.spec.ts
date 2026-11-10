// AS-15, AS-16: the delivery atlas, a 3D tile map of the seller's postcode areas on the Areas report.
import { expect, test, type Page } from "@playwright/test";

const shots = process.env.E2E_SHOTS;

async function openAtlas(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "Open my demo shop" }).click();
  const href = await page.getByRole("link", { name: /Open Maria's Control Room/ }).getAttribute("href");
  await page.goto(`${href!}#insights`);
  const nav = page.getByRole("navigation", { name: "Reports" });
  await expect(nav).toBeVisible();
  await nav.getByRole("button", { name: "Areas", exact: true }).click();
  const atlas = page.locator(".ds-atlas");
  await expect(atlas.locator(".ds-atlas-tile").first()).toBeVisible();
  await atlas.scrollIntoViewIfNeeded();
  return atlas;
}

test("AS-15 the atlas draws a tile per area; hover shows orders, with no buyer details", async ({ page }) => {
  const atlas = await openAtlas(page);
  expect(await atlas.locator(".ds-atlas-tile").count()).toBeGreaterThanOrEqual(1);

  // The tiles stand up once, then hold still.
  await page.waitForTimeout(1600);
  const tile = atlas.locator(".ds-atlas-tile").last();
  await tile.hover();
  const tip = atlas.getByRole("tooltip");
  await expect(tip).toContainText("orders");
  await expect(tip).toContainText("failed");
  await expect(tip).toContainText(/[£$]\d/);

  // Keyboard: a tile takes focus and carries the same numbers in its label.
  await tile.focus();
  await expect(tile).toHaveAttribute("aria-label", /\d+ orders, \d+ failed/);
  await expect(tip).toContainText("orders");

  // Postcode areas only: no email, no buyer name, no street.
  const labels = await atlas.locator(".ds-atlas-tile").evaluateAll((els) => els.map((e) => e.getAttribute("aria-label")));
  const text = `${await atlas.innerText()} ${labels.join(" ")}`;
  expect(text).not.toContain("@");
  for (const name of ["Sam Carter", "Priya Shah", "Sam", "Priya"]) expect(text).not.toContain(name);

  // A click picks the area: the tile is pressed and the others fade, and a second click clears it.
  await tile.dispatchEvent("click");
  await expect(tile).toHaveAttribute("aria-pressed", "true");
  await tile.dispatchEvent("click");
  await expect(tile).toHaveAttribute("aria-pressed", "false");
  if (shots) await page.screenshot({ path: `${shots}/as15-atlas.png` });
});

test("AS-16 on a phone the atlas fits the width", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const atlas = await openAtlas(page);
  await expect(atlas.locator(".ds-atlas-tile").first()).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  const box = await atlas.boundingBox();
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.x + box!.width).toBeLessThanOrEqual(390);
  if (shots) await page.screenshot({ path: `${shots}/as16-atlas-phone.png` });
});
