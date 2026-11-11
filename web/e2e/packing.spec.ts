// PK in a real browser: Malika photographs a paid order before shipping. Without a model key the
// check never auto-passes ("Please check it yourself"); she confirms, then ships.
import { expect, test } from "@playwright/test";

const shots = process.env.E2E_SHOTS;
// An 8x8 dark PNG, so the browser can decode and re-encode it like a phone photo.
const PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAD0lEQVR4nGMQwAEYhpYEALEEDAEl9e6fAAAAAElFTkSuQmCC",
  "base64",
);

test("PK-02 packing photo, seller confirms, ships", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Open my demo shop" }).click();
  const sellerHref = await page.getByRole("link", { name: /Open Malika's Control Room/ }).getAttribute("href");
  await page.goto(sellerHref!);

  await expect(page.getByText("Pack it")).toBeVisible();
  await page.getByText("Black jacket, size L").click();
  await expect(page.getByRole("heading", { name: "Packing check" })).toBeVisible();
  await page.getByTestId("packing-photo").setInputFiles({ name: "jacket.png", mimeType: "image/png", buffer: PNG });

  const verdict = page.getByLabel("Packing check").getByRole("status");
  await expect(verdict).toBeVisible();
  await expect(verdict).toContainText(/Matches the order|Check before shipping|Please check it yourself/);
  await page.waitForTimeout(300);
  if (shots) await page.screenshot({ path: `${shots}/09-packing-check.png` });

  const confirm = page.getByRole("button", { name: "I checked it, it's right" });
  if (await confirm.isVisible()) await confirm.click();
  await page.getByRole("button", { name: "Mark shipped" }).click();
  await expect(page.getByText("Shipped. Tracking is being added")).toBeVisible();
});
