// E2E-01 (scratch path): Not here -> seller approves -> receipt chain verifies, in a real browser.
// The real-PayPal half (checkout + sandbox refund id) runs in the sandbox smoke test (E2E-03).
import { expect, test } from "@playwright/test";

const shots = process.env.E2E_SHOTS;

test("E2E-01 buyer reports, seller approves, receipts verify", async ({ page, context }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("No buyer loses money");
  if (shots) await page.screenshot({ path: `${shots}/01-home.png`, fullPage: true });

  await page.getByRole("button", { name: "Open my demo shop" }).click();
  const sellerHref = await page.getByRole("link", { name: /Open Malika's Control Room/ }).getAttribute("href");
  const samHref = await page.getByRole("link", { name: /Be Diyorbek/ }).getAttribute("href");
  if (shots) await page.screenshot({ path: `${shots}/02-home-seeded.png`, fullPage: true });

  // Buyer: Diyorbek's jacket says delivered, but it isn't there.
  const buyer = await context.newPage();
  await buyer.setViewportSize({ width: 420, height: 900 });
  await buyer.goto(samHref!);
  await expect(buyer.getByRole("heading", { name: "Marked as delivered" })).toBeVisible();
  if (shots) await buyer.screenshot({ path: `${shots}/03-buyer-delivered.png`, fullPage: true });
  await buyer.getByRole("button", { name: "Not here" }).click();
  await buyer.getByRole("radio", { name: "It didn't arrive" }).click();
  if (shots) await buyer.screenshot({ path: `${shots}/04-buyer-report.png`, fullPage: true });
  await buyer.getByRole("button", { name: "Send report" }).click();
  await expect(buyer.getByRole("heading", { name: "We're sorting it out" })).toBeVisible();
  if (shots) await buyer.screenshot({ path: `${shots}/05-buyer-case.png`, fullPage: true });

  // Seller: the case waits for one tap.
  await page.goto(sellerHref!);
  await expect(page.getByRole("heading", { name: "Delivery Control Room" })).toBeVisible();
  await expect(page.getByText("Black jacket, size M")).toBeVisible();
  if (shots) await page.screenshot({ path: `${shots}/06-control-room.png`, fullPage: true });
  await page.getByText("Black jacket, size M").click();
  await expect(page.getByText(/^uk_possession_v1 · /)).toBeVisible();
  await page.waitForTimeout(400);
  if (shots) await page.screenshot({ path: `${shots}/07-case-drawer.png` });
  await page.getByRole("button", { name: /Approve refund/ }).click();
  await expect(page.getByRole("status").filter({ hasText: "Approved" })).toBeVisible();

  // Public check: the signed receipt chain is intact.
  const caseLink = page.getByRole("link", { name: /signed receipt/ });
  await expect(caseLink).toContainText("2 signed receipts");
  const verifyHref = await caseLink.getAttribute("href");
  await page.goto(verifyHref!);
  await expect(page.getByText("Verified.")).toBeVisible();
  if (shots) await page.screenshot({ path: `${shots}/08-verify.png`, fullPage: true });
});

test("BC-01 a forged buyer link shows a friendly error", async ({ page }) => {
  await page.goto("/t/forged-token");
  await expect(page.getByRole("alert")).toContainText("isn't valid");
});
