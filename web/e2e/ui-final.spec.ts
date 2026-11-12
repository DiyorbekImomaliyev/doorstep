// UI-01..: the final design pass in a real browser, on one seeded scratch shop.
import { expect, test, type Page } from "@playwright/test";

let seed: { seller_token: string; buyers: Record<string, string>; summary: { seller_id: string; orders: { key: string; case_id: string | null }[] } };
test.beforeAll(async ({ request }) => {
  seed = await (await request.post("/demo/seed?scratch=1")).json();
});

const noOverflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);

/** Space between the grid's last row and its status bar (0 when the grid fits its rows). */
const gridGap = (page: Page) => page.evaluate(() => {
  const g = document.querySelector(".orders-grid")!;
  const rows = [...g.querySelectorAll(".ag-grid-scrollable-area .ag-row")].filter((x) => x.getBoundingClientRect().height > 0 && !x.closest(".ag-details-row"));
  const bottom = Math.max(...rows.map((x) => x.getBoundingClientRect().bottom));
  return Math.round(g.querySelector(".ag-status-bar")!.getBoundingClientRect().top - bottom);
});

test("UI-01 the Control Room grid is as tall as its rows, with lanes open or closed", async ({ page }) => {
  await page.goto(`/seller/${seed.seller_token}`);
  await page.locator("aside.sidebar").getByRole("button", { name: /^All orders/ }).click();
  const lanes = page.locator(".orders-grid .lane-row");
  await expect(lanes).toHaveCount(3);
  await expect.poll(() => gridGap(page)).toBe(0);
  for (let i = 0; i < 3; i++) await lanes.nth(i).click();
  await expect(page.locator(".orders-grid .ag-row-pinned, .orders-grid .ag-row-footer").last()).toBeVisible();
  await expect.poll(() => gridGap(page)).toBe(0);
  for (let i = 0; i < 3; i++) await lanes.nth(i).click();
  await expect.poll(() => gridGap(page)).toBe(0);
  // A search that matches nothing keeps room for the empty state.
  await page.getByRole("searchbox", { name: "Search orders" }).fill("zzzz");
  await expect(page.getByText("No match.")).toBeVisible();
});

test("UI-02 the landing page shares well and Try the demo makes a shop on the way down", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveTitle(/^Doorstep · /);
  const og = (p: string) => page.locator(`meta[property="${p}"]`).getAttribute("content");
  expect(await og("og:image")).toMatch(/\/assets\/doorstep-share\.png$/);
  expect(await page.locator('meta[name="twitter:card"]').getAttribute("content")).toBe("summary_large_image");
  const image = await page.request.get("/assets/doorstep-share.png");
  expect(image.headers()["content-type"]).toBe("image/png");
  expect((await page.request.get("/assets/favicon.svg")).headers()["content-type"]).toContain("svg");
  await page.getByRole("link", { name: /Try the live demo/ }).click();
  const seller = page.getByRole("link", { name: /Open Malika's Control Room/ });
  await expect(seller).toBeVisible({ timeout: 30_000 });
  await expect(seller).toBeFocused();
  await expect(page.getByRole("button", { name: "Open my demo shop" })).toHaveCount(0);
});

test("UI-02 an invalid link is a calm page with a way on, and every page has its own title", async ({ page }) => {
  await page.goto("/seller/not-a-token");
  await expect(page.getByRole("alert")).toContainText("isn't valid");
  await expect(page.getByRole("link", { name: "Open a demo shop" })).toHaveAttribute("href", "/#demo");
  await expect(page).toHaveTitle("Link not valid · Doorstep");
  await page.goto("/s/not-a-token");
  await expect(page.getByRole("alert")).toContainText("isn't valid");
  await page.goto("/shop/not-a-shop");
  await expect(page.getByRole("heading", { name: "This shop isn't open" })).toBeVisible();
  await page.goto(`/seller/${seed.seller_token}#claims`);
  await expect(page).toHaveTitle("Claims · Doorstep");
  await expect(page.locator('meta[name="robots"]')).toHaveAttribute("content", "noindex");
  await page.goto(`/shop/${seed.summary.seller_id}`);
  await expect(page).toHaveTitle("Checkout · Doorstep");
});

test("UI-03 Money and Autopilot share the live status, Refresh and filter chips of Claims and Disputes", async ({ page }) => {
  for (const hash of ["claims", "disputes", "money", "autopilot"]) {
    await page.goto(`/seller/${seed.seller_token}#${hash}`);
    const head = page.locator(".page-head");
    await expect(head.locator(".live .live-dot"), hash).toBeVisible();
    await expect(head.getByRole("button", { name: /^Refresh/ }), hash).toBeVisible();
  }
  await page.goto(`/seller/${seed.seller_token}#money`);
  await expect(page.getByRole("group", { name: "Filter by status" })).toHaveClass(/\bchips\b/);
  await page.getByRole("button", { name: "Refresh" }).click();
  await page.goto(`/seller/${seed.seller_token}#autopilot`);
  await expect(page.getByRole("group", { name: "Filter replayed cases" })).toHaveClass(/\bchips\b/);
  // The two rule cards share the row: no empty third column.
  const cards = page.getByLabel("Autopilot rules").locator(".ax-rule");
  await expect(cards).toHaveCount(2);
  const [a, b] = [await cards.nth(0).boundingBox(), await cards.nth(1).boundingBox()];
  const row = await page.locator(".ax-rules").boundingBox();
  expect(Math.round(b!.x + b!.width)).toBe(Math.round(row!.x + row!.width));
  expect(Math.round(a!.height)).toBe(Math.round(b!.height));
});

test.describe("phone", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  test("UI-05 the prevention tip and the money chart's dates fit a phone on whole lines", async ({ page }) => {
    await page.goto(`/seller/${seed.seller_token}`);
    const tip = page.getByLabel("Prevention suggestion").locator(".tip-title");
    if (await tip.count()) {
      await tip.scrollIntoViewIfNeeded();
      expect(await tip.evaluate((e) => e.scrollWidth <= e.clientWidth + 1)).toBe(true);
    }
    await page.goto(`/seller/${seed.seller_token}#money`);
    await expect(page.getByLabel(/^Money flow by week/)).toBeVisible();
    expect(await noOverflow(page)).toBe(true);
  });
  test("UI-02 public pages fit a phone, and their controls are 44 px tap targets", async ({ page }) => {
    for (const url of ["/", `/t/${seed.buyers["sam-jacket"]}`, `/t/${seed.buyers["failed-attempt"]}`, `/shop/${seed.summary.seller_id}`, "/proof", "/t/bad-link"]) {
      await page.goto(url);
      await page.locator("main, .landing").first().waitFor();
      await page.waitForTimeout(600);
      expect(await noOverflow(page), url).toBe(true);
      const small = await page.evaluate(() => [...document.querySelectorAll("main .btn, .topbar .btn, .landing .btn")]
        .filter((b) => (b as HTMLElement).offsetParent && b.getBoundingClientRect().height < 44)
        .map((b) => (b.textContent ?? "").trim()));
      expect(small, url).toEqual([]);
    }
  });
});

