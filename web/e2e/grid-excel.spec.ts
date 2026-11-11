// GX-01: the Excel button exports one workbook: "Orders" and "By delivery", with a cover header.
import { execFileSync } from "node:child_process";
import { expect, test } from "@playwright/test";

test("GX-01 the Excel export is one workbook with Orders and By delivery sheets", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Open my demo shop" }).click();
  await page.goto((await page.getByRole("link", { name: /Open Malika's Control Room/ }).getAttribute("href"))!);
  await page.getByRole("group", { name: "Filter orders" }).getByRole("button", { name: /^All orders/ }).click();
  const pills = page.locator(".orders-grid .ag-toolbar .ag-column-drop-cell");
  await expect(page.locator(".orders-grid .ag-row .order-cell").first()).toBeVisible();
  const before = await pills.allTextContents();

  const [dl] = await Promise.all([
    page.waitForEvent("download"),
    page.locator(".orders-grid .ag-toolbar").getByRole("button", { name: "Export to Excel" }).click(),
  ]);
  const file = test.info().outputPath("orders.xlsx");
  await dl.saveAs(file);
  expect(dl.suggestedFilename()).toMatch(/^doorstep-orders-.*\.xlsx$/);
  const read = (part: string) => execFileSync("unzip", ["-p", file, part]).toString();
  const book = read("xl/workbook.xml");
  expect(book).toContain('name="Orders"');
  expect(book).toContain('name="By delivery"');
  const strings = read("xl/sharedStrings.xml");
  expect(strings).toContain("Malika&#39;s Vintage");
  expect(strings).toMatch(/Exported \d/);
  expect(strings).toMatch(/\d orders? · [£$]/);
  expect(strings).not.toMatch(/£[\d.,]+ [+] \$/); // currencies are never summed

  expect(await pills.allTextContents()).toEqual(before); // the grouping is back as it was
});
