// AP-06 and AT-06 in a real browser: the agent checks PayPal on its own when Malika opens Diyorbek's case;
// after she approves, Doorstep offers to handle refunds like it, and she turns the rule on and off.
import { expect, test } from "@playwright/test";

test("AP-06 Malika approves Diyorbek, turns autopilot on, then off", async ({ page, request }) => {
  const seed = await (await request.post("/demo/seed?scratch=1")).json();
  const diyorbek = await request.post(`/api/buyer/${seed.buyers["sam-jacket"]}/not-here`, { data: { reason: "not_received", note: "" } });
  expect(diyorbek.ok()).toBeTruthy();

  await page.goto(`/seller/${seed.seller_token}`);
  await expect(page.getByLabel("Autopilot suggestion")).toHaveCount(0);
  await page.getByText("Black jacket, size M").first().click();
  const agent = page.getByLabel("Doorstep agent");
  await expect(agent.getByText("Asked Doorstep's policy")).toBeVisible(); // AT-06: no button press
  await expect(agent.getByText("Escalated to you")).toBeVisible();

  await page.getByRole("button", { name: /Approve refund £60/ }).click();
  const offer = page.getByLabel("Autopilot suggestion");
  await expect(offer).toContainText("lost parcels up to £60");
  await expect(offer).toContainText("You approved 3 of 3");
  await page.keyboard.press("Escape");
  await offer.getByRole("button", { name: "Turn on" }).click();

  await expect(page.getByLabel("Autopilot suggestion")).toHaveCount(0); // turned on: the offer is gone from the Control Room
  await page.locator("aside.sidebar").getByRole("button", { name: "Autopilot" }).click(); // NV-02: the rules live on their page
  const rules = page.getByLabel("Autopilot rules");
  await expect(rules).toContainText("Autopilot on: lost parcels up to £60");
  await rules.getByRole("switch", { name: "Lost parcels" }).click(); // AP-13: the rule's own switch
  await expect(rules).toContainText("Autopilot is off");
  await page.locator("aside.sidebar").getByRole("button", { name: "All orders" }).click(); // leaving sends the off at once
  await expect.poll(async () => (await (await request.get(`/api/seller/${seed.seller_token}/autopilot`)).json()).rules.length).toBe(0);
  await expect(page.getByLabel("Autopilot suggestion")).toHaveCount(0); // turned off: never offered again
});
