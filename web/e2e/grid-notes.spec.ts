// GN-02 and GN-03: the seller's own cell notes. Added from the right-click menu, kept in this browser
// across a reload, editable, and deletable. Doorstep's own notes stay read-only (GN-01 in grid.spec.ts).
import { expect, test, type Page } from "@playwright/test";

async function openShop(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "Open my demo shop" }).click();
  const href = (await page.getByRole("link", { name: /Open Malika's Control Room/ }).getAttribute("href"))!;
  await page.goto(href);
  await page.getByRole("group", { name: "Filter orders" }).getByRole("button", { name: /^All orders/ }).click();
}

// The first data row, once its cells have loaded; scrolled clear of the sticky page header.
const orderCell = (page: Page) => page.locator('.orders-grid .ag-row:has([col-id="item"] .order-cell)').first().locator('[col-id="item"]');

async function rightClick(page: Page) {
  const cell = orderCell(page);
  await cell.evaluate((el) => el.scrollIntoView({ block: "center" }));
  await cell.click({ button: "right" });
}

test("GN-02 the seller adds a note from the right-click menu; it survives a reload and can be edited", async ({ page }) => {
  await openShop(page);
  await rightClick(page);
  await page.getByRole("menuitem", { name: "Add note" }).click();
  const popup = page.locator(".ag-notes-popup");
  await popup.locator("textarea").fill("Call the buyer before Friday");
  await page.keyboard.press("Escape");
  await expect(popup).toHaveCount(0);
  await expect(orderCell(page)).toHaveClass(/ag-has-cell-notes/);

  await page.reload();
  await page.getByRole("group", { name: "Filter orders" }).getByRole("button", { name: /^All orders/ }).click();
  await orderCell(page).hover();
  await expect(popup).toContainText("You");
  await expect(popup.locator("textarea")).toHaveValue("Call the buyer before Friday");
  await expect(popup.locator("textarea")).not.toHaveAttribute("readonly", "");

  await popup.locator("textarea").fill("Buyer called back");
  await page.keyboard.press("Escape");
  await rightClick(page);
  await expect(page.getByRole("menuitem", { name: "Edit note" })).toBeVisible();
  await page.keyboard.press("Escape");
  await orderCell(page).hover();
  await expect(popup.locator("textarea")).toHaveValue("Buyer called back");
});

test("GN-03 deleting the note removes it, also after a reload", async ({ page }) => {
  await openShop(page);
  await rightClick(page);
  await page.getByRole("menuitem", { name: "Add note" }).click();
  await page.locator(".ag-notes-popup textarea").fill("Temporary");
  await page.keyboard.press("Escape");
  await expect(orderCell(page)).toHaveClass(/ag-has-cell-notes/);

  await rightClick(page);
  await page.getByRole("menuitem", { name: "Delete note" }).click();
  await expect(orderCell(page)).not.toHaveClass(/ag-has-cell-notes/);

  await page.reload();
  await page.getByRole("group", { name: "Filter orders" }).getByRole("button", { name: /^All orders/ }).click();
  await expect(orderCell(page)).not.toHaveClass(/ag-has-cell-notes/);
  await orderCell(page).hover();
  await expect(page.locator(".ag-notes-popup")).toHaveCount(0);
});
