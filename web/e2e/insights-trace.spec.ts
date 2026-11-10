// AS-13, AS-14: the Steps list under an agent reply, and KPIs two to a row on a phone.
import { expect, test, type Page } from "@playwright/test";

const shots = process.env.E2E_SHOTS;

async function openInsights(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "Open my demo shop" }).click();
  const href = await page.getByRole("link", { name: /Open Maria's Control Room/ }).getAttribute("href");
  await page.goto(`${href!}#insights`);
  await expect(page.getByRole("navigation", { name: "Reports" })).toBeVisible();
  await expect(page.locator(".ag-studio-value-widget-content").first()).toHaveText(/\d/);
}

test("AS-13 a finished reply has Steps: labelled, in order, with no buyer details", async ({ page }) => {
  // No model: the console's own reader answers, so the run is steady.
  await page.route("**/insights/agent", (route) =>
    route.fulfill({ json: { message: "I can't reach the model just now.", calls: [], mode: "fallback" } }));
  await openInsights(page);
  await page.getByRole("button", { name: "Ask Doorstep" }).click();
  const panel = page.getByRole("complementary", { name: "Ask Doorstep" });
  await panel.getByRole("button", { name: "Which carrier cost me most this month?" }).click();
  await expect(panel.getByTestId("ask-answer").first()).toBeVisible();
  const trace = panel.getByTestId("ask-trace");
  const toggle = trace.getByRole("button", { name: /^Steps · \d+$/ });
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await expect(trace.locator("ol")).toHaveCount(0);
  await toggle.click();
  const rows = trace.locator("ol li");
  expect(await rows.count()).toBeGreaterThanOrEqual(1);
  await expect(rows.first()).toContainText(/^\s*\S+( \S+){0,2}\s*$/);
  const text = await trace.innerText();
  expect(text).not.toMatch(/@|complete task/i);
  expect(text).not.toMatch(/Maria|Priya|Ahmed|buyer/i);
  if (shots) await page.screenshot({ path: `${shots}/trace-open.png` });
});

test("AS-14 on a phone two KPIs share a row and the page does not scroll sideways", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openInsights(page);
  const kpis = page.locator(".ag-studio-value-widget");
  expect(await kpis.count()).toBeGreaterThanOrEqual(2);
  const a = await kpis.nth(0).boundingBox();
  const b = await kpis.nth(1).boundingBox();
  expect(Math.abs(a!.y - b!.y)).toBeLessThan(2);
  expect(b!.x).toBeGreaterThan(a!.x + a!.width / 2);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  if (shots) await page.screenshot({ path: `${shots}/phone-kpis.png` });
});
