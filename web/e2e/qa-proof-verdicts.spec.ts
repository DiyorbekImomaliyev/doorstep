// QA-07: the public check pages read as sentences: "Not valid. The signature…", "Not checked. No receipts…".
import { expect, test } from "@playwright/test";

test("QA-07 the proof and receipt verdicts start with a capital", async ({ page, request }) => {
  const seed = await (await request.post("/demo/seed?scratch=1")).json();
  await page.goto(`/t/${seed.buyers["sam-jacket"]}`);
  await page.getByRole("button", { name: "Signed delivery record" }).click();
  await expect(page.getByText("Genuine.")).toBeVisible();
  await page.getByRole("button", { name: /Change one character/ }).click();
  await expect(page.locator(".notice.bad")).toContainText(/^Not valid\.\sThe signature/);

  await page.goto("/verify/00000000-0000-0000-0000-000000000000");
  await expect(page.locator(".notice")).toContainText(/^Not checked\.\sNo receipts/);
});
