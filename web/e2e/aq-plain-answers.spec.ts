// AQ-01/02: AI answers read like a product. The grid's note is one line with its full text in a
// tooltip, and a case's explanation names the law, never the internal rule id. Model stubbed.
import { expect, test, type Page } from "@playwright/test";

async function controlRoom(page: Page): Promise<void> {
  await page.goto("/");
  await page.getByRole("button", { name: "Open my demo shop" }).click();
  const href = (await page.getByRole("link", { name: /Open Malika's Control Room/ }).getAttribute("href"))!;
  await page.goto(href);
  await expect(page.locator(".page-head h1")).toBeVisible();
}

test("AQ-01 the grid's note stays on one line and its full text is the tooltip", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const note = "Read as orders waiting on the seller, showing next step and buyer postcode area so London ones can be spotted; no London filter exists in the menu.";
  await page.route("**/grid/ask", (route) => route.fulfill({ json: {
    question: "q", mode: "model", provider: "anthropic",
    plan: { answerable: true, view: "needs_you", journey: [], next_step: [], delivery: [], min_at_stake: null, max_proof: null, sort: [], show: ["buyer"], note },
  } }));
  await controlRoom(page);
  await page.keyboard.press("ControlOrMeta+k");
  const bar = page.getByRole("dialog", { name: "Command bar" });
  await bar.getByRole("combobox", { name: "Command" }).fill("which orders in London need me today?");
  await bar.getByRole("option", { name: /^Ask the grid/ }).click();
  const why = page.getByTestId("reading-note");
  await expect(why).toHaveText(note);
  await expect(why).toHaveAttribute("title", note);
  const text = why.locator("span");
  const { height, lineHeight } = await text.evaluate((el) => ({ height: el.getBoundingClientRect().height, lineHeight: parseFloat(getComputedStyle(el).lineHeight) }));
  expect(height).toBeLessThan(lineHeight * 1.5);
});

test("AQ-02 a case's explanation names the law, not the internal rule id", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  // A model-shaped explanation, as Claude wrote one on Nov 12, over the seeded case.
  await page.route(/\/api\/seller\/[^/]+\/cases\/[^/]+$/, async (route) => {
    const res = await route.fetch();
    const body = await res.json();
    if (body.decision) {
      body.decision.explanation = "The parcel was most likely lost by the carrier. Under the Consumer Rights Act 2015 s.29 [uk_possession_v1], the goods stay at the seller's risk until the buyer has them.";
      body.decision.buyer_message = "Under rule uk_possession_v1, a person is checking the evidence.";
    }
    return route.fulfill({ response: res, json: body });
  });
  await controlRoom(page);
  await page.locator(".ag-row", { hasText: "Denim jacket, size S" }).first().locator(".item-name").click();
  const drawer = page.getByRole("dialog", { name: /^Order / });
  await drawer.locator("summary", { hasText: /^Why/ }).click();
  const why = drawer.locator("details.fold", { has: page.locator("summary", { hasText: /^Why/ }) }).locator("p");
  await expect(why).toContainText("Under the Consumer Rights Act 2015 s.29, the goods stay");
  await expect(why).not.toContainText("uk_possession_v1");
  await drawer.locator("summary", { hasText: "Message to the buyer" }).click();
  const msg = drawer.locator("details.fold", { has: page.locator("summary", { hasText: "Message to the buyer" }) }).locator("p");
  await expect(msg).toHaveText(/^Under Consumer Rights Act 2015 s\.29, a person is checking/);
});
