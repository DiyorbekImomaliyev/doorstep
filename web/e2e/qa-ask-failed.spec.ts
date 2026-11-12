// QA-08: when Ask Doorstep can't read a question (server down), the reply says so; it is never a blank step.
import { expect, test } from "@playwright/test";

test("QA-08 a failed read shows a note under the question", async ({ page }) => {
  await page.route("**/insights/agent", (route) => route.fulfill({ json: { message: "", calls: [], mode: "fallback" } }));
  await page.route("**/insights/ask", (route) => route.fulfill({ status: 500, body: "boom" }));
  await page.goto("/");
  await page.getByRole("button", { name: "Open my demo shop" }).click();
  const href = (await page.getByRole("link", { name: /Open Malika's Control Room/ }).getAttribute("href"))!;
  await page.goto(`${href}#insights`);
  await expect(page.getByRole("navigation", { name: "Reports" })).toBeVisible();
  await page.getByRole("button", { name: "Ask Doorstep" }).click();
  const panel = page.getByRole("complementary", { name: "Ask Doorstep" });
  await panel.getByRole("textbox", { name: "Your question" }).fill("Which carrier cost me most this month?");
  await page.keyboard.press("Enter");
  await expect(panel.locator(".ask-note")).toHaveText("Couldn't read that just now. Try again in a moment.");
  await expect(panel.getByTestId("ask-answer")).toHaveCount(0);
});
