// RG-01/02 in a real browser: the seller reads what their policy says about a case, with the
// passages it came from, and the risk column shows for parcels still on their way.
import { expect, test } from "@playwright/test";

const shots = process.env.E2E_SHOTS;

test("RG-01 the case drawer shows cited policy passages", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Open my demo shop" }).click();
  const sellerHref = await page.getByRole("link", { name: /Open Malika's Control Room/ }).getAttribute("href");
  await page.goto(sellerHref!);
  await expect(page.getByRole("columnheader", { name: "Risk" })).toBeVisible();

  await page.getByText("Denim jacket, size S").click();
  const panel = page.getByLabel("What your policy says");
  await panel.getByRole("button", { name: "Read the policy for this case" }).click();
  await expect(panel.getByText(/not legal advice/)).toBeVisible();
  await expect(panel.locator("li").first().or(panel.getByText(/rule file is quoted/))).toBeVisible();
  await page.waitForTimeout(300);
  if (shots) await page.screenshot({ path: `${shots}/11-policy.png` });
});
