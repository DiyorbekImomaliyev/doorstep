// QA-03: recording what the carrier paid closes the amount form; text in the amount keeps Save off.
import { expect, test } from "@playwright/test";

test("QA-03 Carrier paid: Save needs a number, and the form closes once the payment is recorded", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Open my demo shop" }).click();
  const href = (await page.getByRole("link", { name: /Open Malika's Control Room/ }).getAttribute("href"))!;
  await page.goto(href);
  await page.getByRole("group", { name: "Filter orders" }).getByRole("button", { name: /^All orders/ }).click();
  await page.locator(".ag-row", { hasText: "Denim jacket, size S" }).first().locator(".item-name").click();

  const rec = page.getByRole("dialog", { name: /^Order / }).getByLabel("Recover from the carrier");
  await rec.getByRole("button", { name: "I submitted it" }).click();
  await rec.getByRole("button", { name: "Carrier paid" }).click();
  const amount = rec.getByRole("textbox", { name: /Amount the carrier paid/ });
  const save = rec.getByRole("button", { name: "Save" });
  await amount.fill("seventy");
  await expect(save).toBeDisabled();
  await amount.fill("75");
  await save.click();

  await expect(page.getByRole("status").filter({ hasText: "Recorded: the carrier paid." })).toBeVisible();
  await expect(rec.locator(".badge")).toHaveText("Carrier paid");
  await expect(rec).toContainText("Recovered");
  await expect(amount).toHaveCount(0);
  await expect(save).toHaveCount(0);
});
