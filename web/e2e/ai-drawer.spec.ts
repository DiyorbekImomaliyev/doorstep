// AI-08: the case agent names who planned it: the model (Claude, Gemini) or Doorstep's own plan.
// The agent run is stubbed by route interception: no paid model is called.
import { expect, test } from "@playwright/test";

const run = (planner: string) => ({
  case_id: "c", planner, outcome: "held_for_review",
  steps: [
    { tool: "get_order_details", reason: "r", summary: "Order paid (PayPal: COMPLETED)." },
    { tool: "request_refund", reason: "r", summary: "Held for human review. No refund was created; nobody is denied automatically." },
    { tool: "finish", reason: "r", summary: "Done. The policy's answer: held for review." },
  ],
});

test("AI-08 the case agent says Planned by Claude, or Doorstep's own plan when no model answered", async ({ page }) => {
  let planner = "model:anthropic";
  await page.route("**/agent/cases/**", (route) => route.fulfill({ json: run(planner) }));
  await page.goto("/");
  await page.getByRole("button", { name: "Open my demo shop" }).click();
  const sellerHref = await page.getByRole("link", { name: /Open Malika's Control Room/ }).getAttribute("href");
  await page.goto(sellerHref!);
  await page.getByText("Denim jacket, size S").click();
  const panel = page.getByLabel("Doorstep agent");
  await expect(panel.getByTestId("agent-planner")).toHaveText("Planned by Claude · read-only");
  await expect(panel.getByText("Asked Doorstep's policy")).toBeVisible();

  planner = "fixed plan";
  await panel.getByRole("button", { name: /Run the agent/ }).click();
  await expect(panel.getByTestId("agent-planner")).toHaveText("Doorstep's own plan · no model");
});
