// AS-17..22: what a seller uses every week in Insights, in a real browser on a scratch demo shop.
import { expect, test, type Page } from "@playwright/test";

const shots = process.env.E2E_SHOTS;

async function openInsights(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "Open my demo shop" }).click();
  const sellerHref = await page.getByRole("link", { name: /Open Malika's Control Room/ }).getAttribute("href");
  await page.goto(`${sellerHref!}#insights`);
  await expect(page.getByRole("navigation", { name: "Reports" })).toBeVisible();
  await expect(page.locator(".ds-brief-head")).toHaveText(/orders arrived/);
}

const rail = (page: Page) => page.getByRole("navigation", { name: "Reports" });

test("AS-17 a seller's own report survives a reload, and Reset report goes back to the defaults", async ({ page }) => {
  await openInsights(page);
  await rail(page).getByRole("button", { name: "New report" }).click();
  await expect(rail(page).getByRole("button", { name: "My report 1" })).toBeVisible();
  // Saved shortly after the change, under a key that holds a hash, never the link itself.
  await expect.poll(() => page.evaluate(() => Object.keys(localStorage).filter((k) => k.startsWith("doorstep:insights:")).length)).toBe(1);
  const key = (await page.evaluate(() => Object.keys(localStorage).find((k) => k.startsWith("doorstep:insights:"))))!;
  expect(page.url()).not.toContain(key.split(":").pop()!);

  await page.reload();
  await expect(rail(page).getByRole("button", { name: "My report 1" })).toBeVisible();
  // A second new report never reuses the saved one's id or name.
  await rail(page).getByRole("button", { name: "New report" }).click();
  await expect(rail(page).getByRole("button", { name: "My report 2" })).toBeVisible();

  // Reset asks once more before it acts, then brings the defaults back and clears what was saved.
  await rail(page).getByRole("button", { name: "Reset report" }).click();
  await expect(rail(page).getByRole("button", { name: "My report 1" })).toBeVisible();
  await rail(page).getByRole("button", { name: "Confirm reset" }).click();
  await expect(rail(page).getByRole("button", { name: /^My report/ })).toHaveCount(0);
  await expect(rail(page).getByRole("button", { name: "Overview" })).toBeVisible();
  await page.reload();
  await expect(rail(page).getByRole("button", { name: /^My report/ })).toHaveCount(0);

  // Undo brings the seller's reports back (Reset is one step in the change history).
  await rail(page).getByRole("button", { name: "New report" }).click();
  await rail(page).getByRole("button", { name: "Reset report" }).click();
  await rail(page).getByRole("button", { name: "Confirm reset" }).click();
  await expect(rail(page).getByRole("button", { name: /^My report/ })).toHaveCount(0);
  await page.getByRole("button", { name: "Undo", exact: true }).click();
  await expect(rail(page).getByRole("button", { name: /^My report/ })).toHaveCount(1);
});

test("AS-17 broken or blocked storage never breaks the page", async ({ page }) => {
  await page.addInitScript(() => {
    const boom = () => { throw new Error("storage blocked"); };
    Storage.prototype.getItem = boom;
    Storage.prototype.setItem = boom;
  });
  await openInsights(page);
  await rail(page).getByRole("button", { name: "New report" }).click();
  await expect(rail(page).getByRole("button", { name: "My report 1" })).toBeVisible();
});

test("AS-18 the Carriers report has a claim gauge per carrier, won ÷ claimed on a fixed scale", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openInsights(page);
  await rail(page).getByRole("button", { name: "Carriers", exact: true }).click();
  const gauges = page.locator(".ag-studio-gauge-widget");
  await expect(gauges.first()).toBeVisible();
  const n = await gauges.count();
  expect(n).toBeGreaterThanOrEqual(1);
  // One gauge per carrier with claims, each titled with the carrier's name.
  for (const name of ["Royal Mail", "Evri"]) await expect(page.getByText(name, { exact: true }).first()).toBeVisible();
  await expect(page.getByText("Won ÷ claimed", { exact: true }).first()).toBeVisible();
  if (shots) await page.screenshot({ path: `${shots}/as18-carriers-gauges.png` });
});

test("AS-19 the brief lists up to three open claims, nearest deadline first", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openInsights(page);
  const brief = page.getByRole("region", { name: "Doorstep's brief" });
  const claims = brief.locator("li[data-kind=case], li[data-kind=carrier]");
  await expect(claims.first()).toBeVisible();
  const n = await claims.count();
  expect(n).toBeGreaterThanOrEqual(1);
  expect(n).toBeLessThanOrEqual(3);
  // Days left only ever count up down the list.
  const days = await claims.locator(".ds-days .num").allInnerTexts();
  const left = days.map((d) => (d === "Today" ? 0 : Number(d.replace("d", ""))));
  expect(left).toEqual([...left].sort((a, b) => a - b));
  if (shots) await page.screenshot({ path: `${shots}/as19-brief.png` });
});

test("AS-20 Print shows only the report: no rail, header or console on the printed page", async ({ page }) => {
  // The browser's print dialog cannot be driven: stub it, and look at the page it would print.
  await page.addInitScript(() => { window.print = () => undefined; });
  await openInsights(page);
  await page.getByRole("button", { name: "Ask Doorstep" }).click();
  await page.getByRole("button", { name: "Print" }).click();
  await expect(page.locator("body")).toHaveAttribute("data-printing", "insights");
  await page.emulateMedia({ media: "print" });
  // Studio scrolls in a box; for paper the box is as tall as the whole report.
  expect(await page.locator(".rb-canvas").evaluate((e) => e.getBoundingClientRect().height)).toBeGreaterThan(1200);
  await expect(page.locator(".rb-side")).toBeHidden();
  await expect(page.locator(".ask")).toBeHidden();
  await expect(page.locator(".sidebar")).toBeHidden();
  await expect(page.locator(".page-head")).toBeHidden();
  await expect(page.locator(".ins-print-head")).toBeVisible();
  await expect(page.locator(".ins-print-head")).toContainText("Overview");
  await expect(page.locator(".ds-brief-head")).toBeVisible();
  if (process.env.E2E_SHOTS) await page.pdf({ path: `${shots}/as20-print.pdf`, landscape: true, format: "A4", preferCSSPageSize: true });
  await page.emulateMedia({ media: "screen" });
  await page.evaluate(() => window.dispatchEvent(new Event("afterprint")));
  await expect(page.locator("body")).not.toHaveAttribute("data-printing", /.*/);
  await expect(page.locator(".rb-side")).toBeVisible();
});

test("AS-21 Ask Doorstep's starters follow the shop's own rows", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const posts: string[] = [];
  await page.route("**/insights/agent", async (route) => {
    posts.push(route.request().postData() ?? "");
    await route.fulfill({ json: { message: "I can't reach the model just now.", calls: [], mode: "fallback" } });
  });
  await openInsights(page);
  await page.getByRole("button", { name: "Ask Doorstep" }).click();
  const panel = page.getByRole("complementary", { name: "Ask Doorstep" });
  const starters = panel.locator(".ask-starter");
  await expect(starters).toHaveCount(3);
  const labels = await starters.allInnerTexts();
  // The worst carrier by name, the next claim deadline, the delivery method with the highest case rate.
  expect(labels[0]).toMatch(/^Chase (Royal Mail|Evri|DPD|Shippo \(test\)|USPS|UPS|FedEx)$/);
  expect(labels[1]).toMatch(/ claim, \d{1,2} [A-Z][a-z]{2}$/);
  expect(labels[2]).toMatch(/^(Home|Locker|Signature): highest case rate$/);
  if (shots) await page.screenshot({ path: `${shots}/as21-starters.png` });
  // The claim starter opens that carrier's report, with no model (the backend said none answered).
  await starters.nth(1).click();
  const carrier = labels[1].split(" claim,")[0];
  await expect(page.getByRole("dialog", { name: `${carrier} report` })).toBeVisible();
  // Only the starter's words went to the backend: no rows, no figures.
  for (const body of posts) expect(body).not.toMatch(/order_id|recovery_id|claim_minor/);
});

test("AS-22 every reply says how it was answered: steps, seconds, and the provider or Rules", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  let provider: string | null = "anthropic";
  await page.route("**/insights/agent", async (route) => {
    const body = JSON.parse(route.request().postData() ?? "{}");
    const last = body.items[body.items.length - 1];
    const out = provider && last.kind === "user"
      ? { message: "", calls: [{ call_id: "call_scan", name: "scan_for_anomalies", arguments: {} }], mode: "model", provider }
      : provider
        ? { message: "Nothing stands out.", calls: [], mode: "model", provider }
        : { message: "I can't reach the model just now.", calls: [], mode: "fallback", provider: null };
    await route.fulfill({ json: out });
  });
  await openInsights(page);
  await page.getByRole("button", { name: "Ask Doorstep" }).click();
  const panel = page.getByRole("complementary", { name: "Ask Doorstep" });
  const ask = async (q: string) => {
    await panel.getByRole("textbox", { name: "Your question" }).fill(q);
    await page.keyboard.press("Enter");
  };

  await ask("What changed in the last four weeks?");
  const by = panel.getByTestId("ask-by");
  await expect(by.first()).toHaveText(/^1 step · \d+(\.\d)? s · Claude$|^1 step · \d+ ms · Claude$/);

  provider = "gemini";
  await ask("Anything unusual?");
  await expect(by.nth(1)).toContainText("Gemini");

  provider = null;
  await ask("Which carrier cost me most this month?");
  await expect(by.nth(2)).toHaveAttribute("data-who", "Rules");
  await expect(by.nth(2)).toContainText("Rules");
  if (shots) await page.screenshot({ path: `${shots}/as22-answered-by.png` });
});
