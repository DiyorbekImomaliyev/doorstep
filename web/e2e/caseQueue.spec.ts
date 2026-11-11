// AS-07: the AG Grid case queue lists every open case; Approve goes through the policy service and
// shows the PayPal result; addresses stay masked (the area only, never a street address).
import { expect, test } from "@playwright/test";

const CLOSED = ["SETTLED", "CLOSED"];

test("AS-07 the case queue shows every open case, Approve shows the PayPal result, no address", async ({ page, context, request }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Open my demo shop" }).click();
  const sellerHref = (await page.getByRole("link", { name: /Open Malika's Control Room/ }).getAttribute("href"))!;
  const diyorbekHref = (await page.getByRole("link", { name: /Be Diyorbek/ }).getAttribute("href"))!;

  // Diyorbek reports "Not here": a new case that waits for the seller.
  const buyer = await context.newPage();
  await buyer.goto(diyorbekHref);
  await buyer.getByRole("button", { name: "Not here" }).click();
  await buyer.getByRole("radio", { name: "It didn't arrive" }).click();
  await buyer.getByRole("button", { name: "Send report" }).click();
  await expect(buyer.getByRole("heading", { name: "We're sorting it out" })).toBeVisible();

  // Ground truth from the seller's API: which cases are open, and what a row exposes.
  const token = sellerHref.split("/").filter(Boolean).pop()!.split("#")[0];
  const rows: Array<Record<string, unknown>> = await (await request.get(`/api/seller/${token}/orders`)).json();
  const open = rows.filter((r) => r.case_id && !CLOSED.includes(String(r.case_state)));
  expect(open.length).toBeGreaterThan(0);
  const exposed = new Set(rows.flatMap((r) => Object.keys(r)));
  for (const key of ["address", "address_line1", "street", "postcode", "buyer_address", "buyer_name"]) {
    expect(exposed, `the seller API must not carry ${key}`).not.toContain(key);
  }

  // The Cases queue lists every open case, each with its area only.
  await page.goto(sellerHref);
  await page.getByRole("group", { name: "Filter orders" }).getByRole("button", { name: /^Cases/ }).click();
  for (const r of open) await expect(page.getByText(String(r.order_ref)).first()).toBeVisible();
  const area = String(open[0].buyer_area ?? "");
  if (area) expect(area).not.toMatch(/\d+\s+\w+\s+(street|road|st|rd|lane|avenue)\b/i);

  // Approve: one tap, the policy service decides, and the PayPal result shows in the drawer.
  await page.getByText("Black jacket, size M").first().click();
  await page.getByRole("button", { name: /Approve refund/ }).click();
  // The grid's status bar is a status region too: look for the message that says Approved.
  await expect(page.getByRole("status").filter({ hasText: "Approved" })).toBeVisible();
  await expect(page.getByText(/refund/i).filter({ hasText: /£|\$/ }).first()).toBeVisible();
  await expect(page.locator("body")).not.toContainText(/\b\d{1,4}\s+[A-Z][a-z]+\s+(Street|Road|Lane|Avenue)\b/);
});
