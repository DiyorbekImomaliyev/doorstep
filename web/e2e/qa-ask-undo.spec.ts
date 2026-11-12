// QA-01: an answer that moves the grid to a list with one lane (Needs you) and back still filters the
// rows, both when asked and on Undo, with no error on the page. The answers are mocked.
import { expect, test } from "@playwright/test";

const plan = (o: Record<string, unknown>) => ({
  answerable: true, view: "all", journey: [], next_step: [], delivery: [], min_at_stake: null, max_proof: null, sort: [], show: [], note: "Mocked.", ...o,
});

test("QA-01 Ask across lists and Undo back: the chips and the rows agree, and nothing throws", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.goto("/");
  await page.getByRole("button", { name: "Open my demo shop" }).click();
  const href = (await page.getByRole("link", { name: /Open Malika's Control Room/ }).getAttribute("href"))!;

  const plans = [
    plan({ view: "all", journey: ["Lost by carrier"] }),
    plan({ view: "needs_you" }),
    plan({ view: "cases", journey: ["Lost by carrier"] }),
  ];
  let asked = 0;
  await page.route("**/grid/ask", (route) =>
    route.fulfill({ json: { question: "q", mode: "keywords", provider: null, plan: plans[Math.min(asked++, plans.length - 1)] } }));
  await page.goto(href);
  await page.getByRole("group", { name: "Filter orders" }).getByRole("button", { name: /^All orders/ }).click();
  await expect(page.locator(".ag-row .item-name").first()).toBeVisible();

  const box = page.getByRole("searchbox", { name: "Search orders" });
  const status = page.locator(".orders-grid .ag-status-bar");
  const list = page.locator(".table-card .head h2");
  const chip = page.getByRole("button", { name: "Remove: Journey: Lost by carrier" });
  const ask = async (q: string) => { await box.fill(q); await box.press("Enter"); };

  await ask("lost parcels");
  await expect(chip).toBeVisible();
  await expect(status).toContainText(/Rows\s*:\s*\d+ of \d+/);
  const filteredAll = (await status.innerText()).match(/(\d+) of/)![1];

  await ask("what needs me");
  await expect(list).toHaveText("Needs you");
  await expect(chip).toHaveCount(0);

  await ask("lost parcels in cases");
  await expect(list).toHaveText("Cases");
  await expect(chip).toBeVisible();
  await expect(status).toContainText(/Rows\s*:\s*\d+ of \d+/); // the rows follow the chip

  const undo = page.getByRole("button", { name: "Undo", exact: true });
  await undo.click();
  await expect(list).toHaveText("Needs you");
  await undo.click();
  await expect(list).toHaveText("All orders");
  await expect(chip).toBeVisible();
  await expect(status).toContainText(`${filteredAll} of`);
  await undo.click();
  await expect(chip).toHaveCount(0);
  await expect(status).not.toContainText(/\d+ of \d+/);
  expect(errors).toEqual([]);
});
