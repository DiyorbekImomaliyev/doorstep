// GV-01 to GV-03: saved views in the grid's toolbar, and "new since last visit".
import { expect, test, type Page } from "@playwright/test";

async function openShop(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "Open my demo shop" }).click();
  const href = (await page.getByRole("link", { name: /Open Maria's Control Room/ }).getAttribute("href"))!;
  await showAll(page, href);
  return href;
}

async function showAll(page: Page, href: string) {
  await page.goto(href);
  await page.getByRole("group", { name: "Filter orders" }).getByRole("button", { name: /^All orders/ }).click();
  await expect(rows(page).first()).toBeVisible();
}

const rows = (page: Page) => page.locator('.orders-grid .ag-row:has([col-id="item"] .order-cell)');
const viewsButton = (page: Page) => page.locator(".orders-grid .ag-toolbar").getByRole("button", { name: "Views" });

// Escape leaves the menu open; a click on the toolbar's empty space closes it.
async function closeMenu(page: Page) {
  await page.locator(".orders-grid .ag-toolbar").click({ position: { x: 300, y: 4 } });
  await expect(page.getByRole("menuitemradio").first()).toHaveCount(0);
}

async function chooseView(page: Page, name: string) {
  await viewsButton(page).click();
  await page.getByRole("menuitemradio", { name, exact: true }).click();
}

test("GV-01 a saved view is kept across a reload and puts the grouping back", async ({ page }) => {
  const href = await openShop(page);
  await chooseView(page, "By delivery");
  const pills = page.locator(".orders-grid .ag-toolbar .ag-column-drop-cell");
  await expect(pills).toContainText("Delivery");

  page.once("dialog", (d) => { void d.accept("Mine"); });
  await viewsButton(page).click();
  await page.getByRole("menuitem", { name: "Save view…" }).click();
  await viewsButton(page).click();
  await expect(page.getByRole("menuitemradio", { name: "Mine", exact: true })).toHaveAttribute("aria-checked", "true");
  await closeMenu(page);

  await showAll(page, href); // reload: the default lanes are back
  await expect(pills).not.toContainText("Delivery");
  await chooseView(page, "Mine");
  await expect(pills).toContainText("Delivery");
  await viewsButton(page).click();
  await expect(page.getByRole("menuitemradio", { name: "Mine", exact: true })).toHaveAttribute("aria-checked", "true");
  await closeMenu(page);

  // Changing the grid by hand clears the check; a saved view can be deleted.
  const money = page.locator('.orders-grid .ag-header-cell[col-id="money"]');
  await money.click();
  await expect(money).toHaveAttribute("aria-sort", "ascending");
  await expect(viewsButton(page)).not.toHaveAttribute("aria-label", /Mine/); // the toolbar names the active view
  await viewsButton(page).click();
  await expect(page.getByRole("menuitemradio", { name: "Mine", exact: true })).toHaveAttribute("aria-checked", "false");
  await page.getByRole("menuitem", { name: "Delete" }).click();
  await page.getByRole("menuitem", { name: "Mine", exact: true }).click();
  await viewsButton(page).click();
  await expect(page.getByRole("menuitemradio", { name: "Mine", exact: true })).toHaveCount(0);
});

test("GV-02 the Needs me view shows only the orders that need the seller", async ({ page }) => {
  await openShop(page);
  const all = await rows(page).count();
  await chooseView(page, "Needs me");
  await expect.poll(() => rows(page).count()).toBeLessThan(all);
  expect(await rows(page).count()).toBeGreaterThan(0);
  await viewsButton(page).click();
  await expect(page.getByRole("menuitemradio", { name: "Needs me" })).toHaveAttribute("aria-checked", "true");
  await closeMenu(page);
  // Another preset puts the rest back.
  await chooseView(page, "By delivery");
  await expect.poll(() => rows(page).count()).toBe(all);
});

test("GV-03 orders updated since the last visit get a dot and a New filter", async ({ page }) => {
  const href = await openShop(page);
  const token = /\/seller\/([^/?#]+)/.exec(href)![1];
  // First visit: nothing is new.
  await expect(page.locator(".orders-grid .new-dot")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /^New · / })).toHaveCount(0);

  // Leaving the page counts as looking, so the old time is put back as the next page starts.
  await page.addInitScript((t) => { if (!localStorage.getItem("gv-once")) { localStorage.setItem("gv-once", "1"); localStorage.setItem(`doorstep.seen.${t}`, "2000-01-01T00:00:00.000Z"); } }, token);
  await showAll(page, href);
  const btn = page.getByRole("button", { name: /^New · \d+$/ });
  await expect(btn).toBeVisible();
  await expect(page.locator(".orders-grid .order-meta .new-dot[title='New']").first()).toBeVisible();
  const n = Number((await btn.innerText()).replace(/\D/g, ""));
  expect(n).toBeGreaterThan(0);

  // The filter combines with a view's: Needs me and New.
  await btn.click();
  await expect(btn).toHaveAttribute("aria-pressed", "true");
  await expect.poll(() => rows(page).count()).toBe(n);
  await chooseView(page, "Needs me");
  await expect.poll(() => rows(page).count()).toBeLessThanOrEqual(n);
  await chooseView(page, "By delivery");
  await expect.poll(() => rows(page).count()).toBe(n);

  // Mark seen: the dots and the button go; the time is kept.
  await page.getByRole("button", { name: "Mark seen" }).click();
  await expect(btn).toHaveCount(0);
  await expect(page.locator(".orders-grid .new-dot")).toHaveCount(0);
  const seen = await page.evaluate((t) => localStorage.getItem(`doorstep.seen.${t}`), token);
  expect(Date.parse(seen!)).toBeGreaterThan(Date.now() - 60_000);
});
