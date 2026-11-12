// QA-02: when the AI policy check fails, the Policy page says the AI was unavailable (nothing is
// still waiting) and offers a retry; findings the code could not match say "Not verified".
import { expect, test } from "@playwright/test";

test("QA-02 an unchecked policy says AI unavailable or Not verified, and Retry runs the check again", async ({ page, request }) => {
  const seed = await (await request.post("/demo/seed?scratch=1")).json();
  const t: string = seed.seller_token;
  const real = await (await request.get(`/api/seller/${t}/policy-check`)).json();
  let dropped = 0;
  let calls = 0;
  await page.route(/\/api\/seller\/[^/]+\/policy-check$/, (route) => {
    calls++;
    return route.fulfill({ json: { ...real, status: "unchecked", issues: [], provider: null, dropped } });
  });
  await page.goto(`/seller/${t}#policy`);

  const summary = page.getByLabel("Summary");
  const band = page.getByLabel("Policy check").getByRole("status");
  await expect(summary).toContainText("AI unavailable");
  await expect(summary).not.toContainText("waiting");
  await expect(band).toContainText("AI unavailable");
  await expect(band).toContainText("policy not checked");

  const before = calls;
  dropped = 2;
  await band.getByRole("button", { name: "Retry" }).click();
  await expect(page.locator(".toast")).toContainText("Not checked · not verified");
  await expect(band).toContainText("Not verified");
  await expect(summary).toContainText("Not verified");
  expect(calls).toBe(before + 1);
});
