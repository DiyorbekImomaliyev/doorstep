// v2 liability router in a real browser: the seal, the delivered-scan audit, carrier recovery,
// PayPal dispute evidence, the rights card and the counterfactual (SL, SA, RC, DE, RT, CF).
import { expect, test, type Page } from "@playwright/test";

const shots = process.env.E2E_SHOTS;

async function demoShop(page: Page): Promise<{ seller: string; sam: string }> {
  await page.goto("/");
  await page.getByRole("button", { name: "Open my demo shop" }).click();
  const seller = await page.getByRole("link", { name: /Open Maria's Control Room/ }).getAttribute("href");
  const sam = await page.getByRole("link", { name: /Be Sam/ }).getAttribute("href");
  return { seller: seller!, sam: sam! };
}

test("SL-02 the seal in the box: one tap proves the buyer has it", async ({ page, context }) => {
  const { seller } = await demoShop(page);
  await page.goto(seller);
  await page.getByRole("button", { name: /^All/ }).click();  // delivered, nothing needs Maria
  await page.getByText("Brass desk lamp").click();
  const seal = page.getByLabel("Possession seal");
  await seal.getByRole("button", { name: "Show seal" }).click();
  await expect(seal.getByText("Opened it? Scan to confirm")).toBeVisible();
  await expect(seal.locator("svg")).toBeVisible();
  if (shots) await page.screenshot({ path: `${shots}/20-seal-card.png` });
  const href = await seal.getByRole("link", { name: /Open seal page/ }).getAttribute("href");

  const phone = await context.newPage();
  await phone.setViewportSize({ width: 390, height: 844 });
  await phone.goto(new URL(href!).pathname);
  await expect(phone.getByRole("heading", { name: "Brass desk lamp" })).toBeVisible();
  if (shots) await phone.screenshot({ path: `${shots}/21-seal-phone.png`, fullPage: true });
  await phone.getByRole("button", { name: "I have it" }).click();
  await expect(phone.getByText("The seller knows you have it")).toBeVisible();
  await expect(phone.getByText(/address/i)).toHaveCount(0);
});

test("SA-02 RC-05 RT-01 CF-01 Sam's scan is contradicted and Maria claims it back", async ({ page, context }) => {
  const { seller, sam } = await demoShop(page);

  const buyer = await context.newPage();
  await buyer.setViewportSize({ width: 420, height: 900 });
  await buyer.goto(sam);
  await buyer.getByRole("button", { name: "Not here" }).click();
  await buyer.getByRole("radio", { name: "It didn't arrive" }).click();
  await buyer.getByRole("button", { name: "Send report" }).click();
  const rights = buyer.getByLabel("Your rights");
  await expect(rights).toContainText("Consumer Rights Act 2015");
  await expect(rights).toContainText("do not need to contact the courier");
  if (shots) await buyer.screenshot({ path: `${shots}/22-rights-card.png`, fullPage: true });

  await page.goto(seller);
  await page.getByText("Black jacket, size M").click();
  const audit = page.getByLabel("Delivered-scan audit");
  await expect(audit.getByText("Contradicted")).toBeVisible();
  await expect(audit).toContainText("scanned as delivered in E8, but the parcel was going to N1");
  await expect(audit).toContainText("3 different areas");
  await expect(page.getByLabel("What would have changed this")).toContainText("a scan of the seal packed inside the parcel");

  const claim = page.getByLabel("Recover from the carrier");
  await expect(claim.getByRole("heading", { name: "Recover from Royal Mail" })).toBeVisible();
  await expect(claim).toContainText("£60.00");
  await claim.getByText(/Claim pack/).click();
  if (shots) await page.screenshot({ path: `${shots}/23-recovery.png` });
  await claim.getByRole("button", { name: "I submitted it" }).click();
  await expect(claim.getByText("Submitted", { exact: true })).toBeVisible();
  await claim.getByRole("button", { name: "Carrier paid" }).click();
  await claim.getByRole("button", { name: "Save" }).click();
  await expect(claim.getByText("Carrier paid", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Close" }).click();
  await page.getByRole("button", { name: "Refresh" }).click();
  await expect(page.locator(".kpi", { hasText: "Recovered from carriers" })).toContainText("£60");
});

test("DE-01 possession proven, PayPal dispute open: Maria sends the facts once", async ({ page }) => {
  const { seller } = await demoShop(page);
  await page.goto(seller);
  await page.getByText("Wireless headphones").click();  // in "Needs you": PayPal waits for Maria
  await expect(page.getByText("The buyer opened a PayPal dispute")).toBeVisible();
  await expect(page.getByLabel("Delivered-scan audit").getByText("Consistent")).toBeVisible();
  await expect(page.getByLabel("What would have changed this")).toContainText("Possession was proven");
  await page.getByRole("button", { name: "Send evidence to PayPal" }).click();
  await expect(page.getByText(/Evidence (sent to|queued for) PayPal/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Send evidence to PayPal" })).toHaveCount(0);
  await expect(page.getByText("Evidence to the PayPal dispute")).toBeVisible();
  if (shots) await page.screenshot({ path: `${shots}/24-dispute-evidence.png` });
});
