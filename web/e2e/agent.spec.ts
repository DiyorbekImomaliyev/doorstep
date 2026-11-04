// AT-04 in a real browser: on a case above the seller's limit (or held), the agent reads the
// PayPal side, asks the policy, and stops. No refund appears.
import { expect, test } from "@playwright/test";

const shots = process.env.E2E_SHOTS;

test("AT-04 the agent checks PayPal, asks the policy and stops", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Open my demo shop" }).click();
  const sellerHref = await page.getByRole("link", { name: /Open Maria's Control Room/ }).getAttribute("href");
  await page.goto(sellerHref!);

  await page.getByText("Denim jacket, size S").click();
  const panel = page.getByLabel("Doorstep agent");
  await panel.getByRole("button", { name: "Run the agent" }).click();
  await expect(panel.getByText("Asked Doorstep's policy")).toBeVisible();
  await expect(panel.getByText(/Held for review|Escalated to you/)).toBeVisible();
  await expect(page.getByText("Agent: asked the policy for the refund")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Money" })).toHaveCount(0);
  await page.waitForTimeout(300);
  if (shots) await page.screenshot({ path: `${shots}/10-agent.png` });
});
