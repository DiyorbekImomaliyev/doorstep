// GR-02 and GA-01: the Control Room grid speaks once (the Doorstep row) and answers a question as its
// own filters and sort. The answer is mocked, so a live model can't change what this checks.
import { expect, test, type Page } from "@playwright/test";

async function openShop(page: Page): Promise<string> {
  await page.goto("/");
  await page.getByRole("button", { name: "Open my demo shop" }).click();
  const sellerHref = (await page.getByRole("link", { name: /Open Maria's Control Room/ }).getAttribute("href"))!;
  await page.goto(sellerHref);
  return sellerHref;
}

test("GR-02 Doorstep speaks once at the top of the grid, and Start opens the first order to act on", async ({ page }) => {
  await openShop(page);
  const row = page.locator(".orders-grid .ds-row");
  await expect(row).toContainText(/needs? you\./);
  await expect(row).toContainText("Start with");
  await expect(row).not.toContainText("Wireless headphones"); // it never names an item
  await row.getByRole("button", { name: "Start", exact: true }).click();
  await expect(page.getByRole("dialog", { name: /^Order / })).toBeVisible();
});

test("GA-01 a question becomes grid filters and sort, shown as chips read from the grid; Undo puts it back", async ({ page, request }) => {
  const sellerHref = await openShop(page);
  const token = sellerHref.split("/").filter(Boolean).pop()!.split("#")[0];
  await page.getByRole("group", { name: "Filter orders" }).getByRole("button", { name: /^All orders/ }).click();
  const count = page.locator(".table-card .head .count-chip");
  const total = (await count.innerText()).trim();

  const sent: Array<{ question: string; menu: Record<string, string[]> }> = [];
  await page.route("**/grid/ask", async (route) => {
    sent.push(route.request().postDataJSON());
    await route.fulfill({
      json: {
        question: "lost parcels over £50, biggest first", mode: "keywords", provider: null,
        plan: {
          answerable: true, view: "all", journey: ["Lost by carrier"], next_step: [], delivery: [],
          min_at_stake: 50, max_proof: null, sort: [{ col: "money", desc: true }], show: [], note: "Read by keywords.",
        },
      },
    });
  });
  const box = page.getByRole("searchbox", { name: "Search orders" });
  await box.fill("lost parcels over £50, biggest first");
  await box.press("Enter");

  await expect(page.getByRole("button", { name: /^Remove: At stake ≥ / })).toBeVisible();
  await expect(page.getByRole("button", { name: "Remove: Journey: Lost by carrier" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Remove: Most at stake first" })).toBeVisible();
  await expect(page.getByText("Read by keywords")).toBeVisible();
  await expect(page.locator(".ag-status-bar")).toContainText(/\d+ of \d+/);
  await expect(box).toHaveValue("");

  // What left the browser: the question and the grid's own labels. Never an item, order ref or area.
  expect(sent).toHaveLength(1);
  expect(Object.keys(sent[0].menu).sort()).toEqual(["method", "next", "status"]);
  expect(sent[0].menu.status).toContain("Lost by carrier");
  const rows: Array<{ item: string; order_ref: string; buyer_area: string | null }> =
    await (await request.get(`/api/seller/${token}/orders`)).json();
  const body = JSON.stringify(sent[0]);
  for (const r of rows) {
    expect(body).not.toContain(r.item);
    expect(body).not.toContain(r.order_ref);
    if (r.buyer_area) expect(body).not.toContain(`"${r.buyer_area}"`);
  }

  await page.getByRole("button", { name: "Undo", exact: true }).click();
  await expect(page.getByRole("button", { name: /^Remove: / })).toHaveCount(0);
  await expect(count).toHaveText(total);
  await expect(page.locator(".ag-status-bar")).not.toContainText(/\d+ of \d+/);
});

test("GN-01 hovering a held case's Journey cell shows Doorstep's note, which can't be edited", async ({ page, request }) => {
  const sellerHref = await openShop(page);
  const token = sellerHref.split("/").filter(Boolean).pop()!.split("#")[0];
  const rows: Array<{ item: string; case_state: string | null }> = await (await request.get(`/api/seller/${token}/orders`)).json();
  const held = rows.find((r) => r.case_state === "HOLD_FOR_REVIEW");
  expect(held, "the demo shop has a case held for review").toBeTruthy();
  await page.getByRole("group", { name: "Filter orders" }).getByRole("button", { name: /^All orders/ }).click();
  await page.locator(".ag-row", { hasText: held!.item }).first().locator('[col-id="status"]').hover();
  const popup = page.locator(".ag-notes-popup");
  await expect(popup).toContainText("Doorstep");
  await expect(popup.locator("textarea")).toHaveValue(/^Held for a person to review\. Nothing has been refused\./);
  await expect(popup.locator("textarea")).toHaveAttribute("readonly", "");
  await expect(popup.getByRole("button")).toHaveCount(0);
});

test("GP-01 the hand-off report downloads as a PDF of what the grid shows", async ({ page }) => {
  await openShop(page);
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download hand-off report" }).click();
  const file = await download;
  expect(file.suggestedFilename()).toMatch(/^doorstep-handoff-\d{4}-\d{2}-\d{2}\.pdf$/);
  const bytes = await (await import("node:fs/promises")).readFile((await file.path())!);
  expect(bytes.subarray(0, 5).toString()).toBe("%PDF-");
  expect(bytes.length).toBeGreaterThan(2000);
});

test("GM-01 on a phone the grid comes first: no sideways scroll, and the first order shows without scrolling", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openShop(page);
  const first = page.locator(".ag-row .item-name").first();
  await expect(first).toBeVisible();
  const box = (await first.boundingBox())!;
  expect(box.y + box.height).toBeLessThan(844);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});

