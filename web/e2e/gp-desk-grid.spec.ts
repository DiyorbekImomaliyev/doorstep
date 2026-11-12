// GP-01..GP-06 in a real browser: Claims, Disputes, Money and the Autopilot replay grid share the
// Control Room's grid kit. Right-click menu (Open case, Copy, Excel, CSV, Reset layout), the Excel
// file (name, sheet, money as numbers), a range's money sum in the status bar, the Columns + Filters
// side bar, the layout kept per page, and Enter on a row opening its detail.
import { execFileSync } from "node:child_process";
import { expect, test, type Page } from "@playwright/test";

let token = "";
test.describe.configure({ mode: "serial" });
test.use({ viewport: { width: 1440, height: 900 }, permissions: ["clipboard-read", "clipboard-write"] });

test.beforeAll(async ({ request }) => {
  const seed = await (await request.post("/demo/seed?scratch=1")).json();
  token = seed.seller_token as string;
  await expect.poll(async () => (await (await request.get(`/api/seller/${token}/claims/desk`)).json()).rows.length, { timeout: 30_000 }).toBe(5);
  await expect.poll(async () => {
    const d = await (await request.get(`/api/seller/${token}/money-desk`)).json();
    return d.rows.length >= 4 && !d.rows.some((r: { status: string }) => r.status === "queued" || r.status === "pending");
  }, { timeout: 30_000 }).toBe(true);
});

const claims = (page: Page) => page.getByLabel("Carrier claims");
const dataRow = (page: Page, item: string) => claims(page).locator(".ag-row:not(.ag-row-pinned):not(.ag-full-width-row)", { hasText: item });
const menu = (page: Page) => page.locator(".ag-menu");
const pound = (s: string) => Math.round(Number(s.replace(/[^\d.]/g, "")) * 100);

async function openClaims(page: Page) {
  await page.goto(`/seller/${token}#claims`);
  await expect(dataRow(page, "Silk scarf")).toBeVisible();
}

test("GP-01 a right-click on a claim offers Open case, Copy, Excel, CSV and Reset layout; Open case opens the case", async ({ page }) => {
  await openClaims(page);
  await dataRow(page, "Leather belt").locator('[col-id="carrier"]').click({ button: "right" });
  for (const name of ["Open case", "Copy", "Copy with headers", "Export to Excel", "Export to CSV", "Columns and filters", "Reset layout"]) {
    await expect(menu(page).getByText(name, { exact: true })).toBeVisible();
  }
  await menu(page).getByText("Open case", { exact: true }).click();
  await expect(page.locator(".drawer")).toBeVisible();
  await expect(page.locator(".drawer")).toContainText("Leather belt");
});

test("GP-02 Export to Excel names the file after the page, one sheet, money as numbers in its currency", async ({ page }) => {
  await openClaims(page);
  const tools = page.getByRole("toolbar", { name: "Grid tools" });
  await expect(tools.getByRole("button", { name: "Export to Excel" })).toHaveAttribute("title", "Export to Excel");
  const [dl] = await Promise.all([page.waitForEvent("download"), tools.getByRole("button", { name: "Export to Excel" }).click()]);
  expect(dl.suggestedFilename()).toMatch(/^Doorstep claims \d{4}-\d{2}-\d{2}\.xlsx$/);
  const file = test.info().outputPath("claims.xlsx");
  await dl.saveAs(file);
  const unzip = (part: string) => execFileSync("unzip", ["-p", file, part]).toString();
  expect(unzip("xl/workbook.xml")).toContain('name="Claims"');
  const strings = unzip("xl/sharedStrings.xml");
  expect(strings).toContain("Silk scarf, green");
  expect(strings).toContain("Total GBP");
  expect(strings).not.toContain("I submitted it"); // the row buttons stay out of the file
  // £75 is a number cell (not the text "£75") with a pound number format.
  const sheet = unzip("xl/worksheets/sheet1.xml");
  expect(sheet).toMatch(/<v>75<\/v>/);
  expect(strings).not.toMatch(/£75/);
  expect(unzip("xl/styles.xml")).toContain("&quot;£&quot;#,##0.00");

  // Disputes: the same button, its own file and sheet.
  await page.goto(`/seller/${token}#disputes`);
  await expect(page.getByLabel("PayPal disputes").locator(".ag-row").first()).toBeVisible();
  const [d2] = await Promise.all([page.waitForEvent("download"), page.getByRole("toolbar", { name: "Grid tools" }).getByRole("button", { name: "Export to Excel" }).click()]);
  expect(d2.suggestedFilename()).toMatch(/^Doorstep disputes \d{4}-\d{2}-\d{2}\.xlsx$/);
});

test("GP-03 a range over claim amounts shows their count, sum and average in the status bar", async ({ page }) => {
  await openClaims(page);
  const a = dataRow(page, "Denim jacket").locator('[col-id="claim"]');
  const b = dataRow(page, "Ceramic mug").locator('[col-id="claim"]');
  const sum = pound(await a.innerText()) + pound(await b.innerText());
  const box1 = (await a.boundingBox())!, box2 = (await b.boundingBox())!;
  await page.mouse.move(box1.x + box1.width / 2, box1.y + box1.height / 2);
  await page.mouse.down();
  await page.mouse.move(box2.x + box2.width / 2, box2.y + box2.height / 2, { steps: 8 });
  await page.mouse.up();
  const bar = claims(page).locator(".ag-status-bar");
  await expect(bar).toContainText(`£${sum / 100}`); // the sum, in pounds
  await expect(bar).toContainText(`£${sum / 200}`); // the average
  await expect(bar).toContainText("Count");
  // A drag selects; it does not open the claim.
  await expect(dataRow(page, "Denim jacket").locator(".expander")).toHaveAttribute("aria-expanded", "false");
});

test("GP-04 Columns and filters opens AG Grid's side bar and closes it again", async ({ page }) => {
  await openClaims(page);
  const button = page.getByRole("toolbar", { name: "Grid tools" }).getByRole("button", { name: "Columns and filters" });
  await button.click();
  const side = claims(page).locator(".ag-side-bar");
  await expect(side.locator(".ag-column-select")).toBeVisible();
  await expect(side.locator(".ag-column-select")).toContainText("Carrier");
  await side.getByRole("tab", { name: "Filters" }).or(side.locator(".ag-side-button", { hasText: "Filters" })).first().click();
  await expect(side).toContainText(/Add filter/i);
  await button.click();
  await expect(side.locator(".ag-column-select")).toBeHidden();
});

test("GP-05 a sorted column is kept for the page after a reload; Reset layout puts it back", async ({ page }) => {
  await openClaims(page);
  const head = claims(page).locator('.ag-header-cell[col-id="claim"]');
  await head.click();
  await expect(head).toHaveAttribute("aria-sort", "ascending");
  await page.reload();
  await expect(dataRow(page, "Silk scarf")).toBeVisible();
  await expect(head).toHaveAttribute("aria-sort", "ascending");
  await dataRow(page, "Silk scarf").locator('[col-id="carrier"]').click({ button: "right" });
  await menu(page).getByText("Reset layout", { exact: true }).click();
  await expect(head).not.toHaveAttribute("aria-sort", "ascending");
  await page.reload();
  await expect(dataRow(page, "Silk scarf")).toBeVisible();
  await expect(head).not.toHaveAttribute("aria-sort", "ascending");
});

test("GP-06 Enter on a focused claim opens and closes its story", async ({ page }) => {
  await openClaims(page);
  const expander = dataRow(page, "Silk scarf").locator(".expander");
  await dataRow(page, "Silk scarf").locator('[col-id="fault"]').click();
  await expect(expander).toHaveAttribute("aria-expanded", "true");
  await page.keyboard.press("Enter");
  await expect(expander).toHaveAttribute("aria-expanded", "false");
  await page.keyboard.press("Enter");
  await expect(expander).toHaveAttribute("aria-expanded", "true");
});

test("GP-01 GP-03 Money: the menu's Excel is the ledger workbook, and a range sums the amounts", async ({ page }) => {
  await page.goto(`/seller/${token}#money`);
  const grid = page.getByLabel("Money actions");
  const rows = grid.locator(".ag-row:not(.ag-row-pinned):has(.ag-cell[col-id=status])");
  await expect(rows.first()).toBeVisible();
  await rows.first().locator('[col-id="kind"]').click({ button: "right" });
  await expect(menu(page).getByText("Open case", { exact: true })).toBeVisible();
  const [dl] = await Promise.all([page.waitForEvent("download"), menu(page).getByText("Export to Excel", { exact: true }).click()]);
  expect(dl.suggestedFilename()).toMatch(/^doorstep-money-\d{4}-\d{2}-\d{2}\.xlsx$/);
  const a = rows.nth(0).locator('[col-id="amount_minor"]'), b = rows.nth(1).locator('[col-id="amount_minor"]');
  const sum = pound(await a.innerText()) + pound(await b.innerText());
  await a.click();
  await b.click({ modifiers: ["Shift"] });
  await expect(grid.locator(".ag-status-bar")).toContainText(`£${sum / 100}`);
  // Money has its own Export in the page head: the grid tools add Columns and filters only.
  const tools = page.getByRole("toolbar", { name: "Grid tools" });
  await expect(tools.getByRole("button", { name: "Columns and filters" })).toBeVisible();
  await expect(tools.getByRole("button", { name: "Export to Excel" })).toHaveCount(0);
});

test("GP-01 the Autopilot replay grid has the same menu", async ({ page }) => {
  await page.goto(`/seller/${token}#autopilot`);
  const grid = page.getByLabel("Replayed cases");
  const first = grid.locator(".ag-row:not(.ag-row-pinned)").first();
  await expect(first).toBeVisible();
  await first.locator('[col-id="fp"]').click({ button: "right" });
  for (const name of ["Open case", "Copy", "Export to Excel", "Reset layout"]) await expect(menu(page).getByText(name, { exact: true })).toBeVisible();
  const [dl] = await Promise.all([page.waitForEvent("download"), menu(page).getByText("Export to Excel", { exact: true }).click()]);
  expect(dl.suggestedFilename()).toMatch(/^Doorstep replayed cases \d{4}-\d{2}-\d{2}\.xlsx$/);
});
