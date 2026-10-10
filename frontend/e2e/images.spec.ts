import { BASE_URL, expect, expectLayoutHolds, screenshot, test } from "./support";

/**
 * Brand images load once (DESIGN.md §8): in one page session each image of
 * `components/brand` is asked of the server at most once, and an image that has loaded is
 * in its element when that element is first painted.
 *
 * A person the spec made walks the app without a reload: Markets, a market, the trade
 * sheet, a buy of a shortcut, a sale of half, Wallet, Leaderboard, Portfolio, Markets and
 * the same market again. The spec routes and intercepts no request: that would switch
 * the browser's cache off, which is what is measured here.
 */

/** How long the person stays on a screen before Chrome is asked to collect its garbage. */
const STAY_MS = 800;

/** What the page notes about its brand images while the person walks. */
type BrandReport = {
  /** The addresses each brand image was shown from, by the name in `data-brand`. */
  shown: Record<string, string[]>;
  /** Images that had loaded before and were not in their element at its first frame. */
  late: string[];
};

/**
 * Runs in the page before any script of the app, so it uses nothing of this file. It
 * makes room for the resource timings of the whole walk, and watches every `img` marked
 * `data-brand` that is attached to the document: one whose address has already loaded in
 * this document has to be complete at the first animation frame after it was attached.
 */
function watchBrandImages(): void {
  performance.setResourceTimingBufferSize(5000);

  const report: { shown: Record<string, string[]>; late: string[] } = { shown: {}, late: [] };
  Object.defineProperty(window, "brandReport", { value: report });
  const loaded = new Set<string>();

  const watch = (img: HTMLImageElement) => {
    const name = img.getAttribute("data-brand");
    if (name === null) return;
    const address = img.currentSrc || img.src;
    const addresses = (report.shown[name] ??= []);
    if (!addresses.includes(address)) addresses.push(address);

    const isThere = () => img.complete && img.naturalWidth > 0;
    if (loaded.has(address)) {
      requestAnimationFrame(() => {
        if (!isThere()) report.late.push(`${name} on ${window.location.pathname}`);
      });
    } else if (isThere()) {
      loaded.add(address);
    } else {
      img.addEventListener("load", () => loaded.add(address), { once: true });
    }
  };

  new MutationObserver((records) => {
    for (const record of records) {
      for (const node of record.addedNodes) {
        if (!(node instanceof Element)) continue;
        if (node instanceof HTMLImageElement) watch(node);
        node.querySelectorAll("img").forEach(watch);
      }
    }
  }).observe(document, { childList: true, subtree: true });
}

test("a brand image is asked for once and is there at the first paint", async ({
  people,
}, testInfo) => {
  const { context } = await people.join();
  await context.addInitScript(watchBrandImages);

  // A market the person may trade, taken from the list the Markets screen reads.
  const listed = await context.request.get(`${BASE_URL}/api/markets?status=OPEN&status=HALTED`);
  expect(listed.status(), await listed.text()).toBe(200);
  const { items }: { items: { id: string; trading_open: boolean }[] } = await listed.json();
  const market = items.find((item) => item.trading_open);
  expect(market, "the seed has a market that trades").toBeDefined();
  const marketAddress = `/markets/${market!.id}`;

  const page = await context.newPage();
  // A person stays on a screen for a while, and meanwhile Chrome lets go of every loaded
  // image that no element shows (the logo, on a screen with a back arrow). The walk has
  // no time to lose, so it asks Chrome to do that on every screen: an image the page does
  // not hold is then gone when the next screen shows it, as it is for a person.
  const chrome = await context.newCDPSession(page);
  const stay = async () => {
    await page.waitForTimeout(STAY_MS);
    await chrome.send("HeapProfiler.collectGarbage");
  };
  const tab = async (address: string, title: string) => {
    await page.getByRole("navigation", { name: "Main" }).locator(`a[href="${address}"]`).click();
    await page.waitForURL((url) => url.pathname === address);
    await expect(page.getByRole("heading", { level: 1, name: title })).toBeAttached();
    await stay();
  };
  const openMarket = async () => {
    await page.locator(`[data-slot="market-list"] a[href="${marketAddress}"]`).first().click();
    await page.waitForURL((url) => url.pathname === marketAddress);
    await expect(page.getByRole("button", { name: /^YES / }).first()).toBeVisible();
    await stay();
  };

  // Markets, a market, the trade sheet.
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1, name: "Markets" })).toBeAttached();
  await stay();
  await openMarket();
  await page.getByRole("button", { name: /^YES / }).first().click();

  // A buy of a shortcut. The ticket stays where it is, with the receipt above the form.
  const buy = page.getByRole("button", { name: "Buy YES" });
  await expect(buy).toBeVisible();
  await page
    .getByRole("button", { name: /^\d+ shares$/ })
    .and(page.locator(":enabled"))
    .first()
    .click();
  await expect(buy).toBeEnabled();
  await buy.click();
  await expect(page.getByText(/^Bought .+ YES shares/).first()).toBeVisible();

  // A sale of half of it.
  await page.getByText("Sell", { exact: true }).first().click();
  await page.getByRole("button", { name: /^Half/ }).click();
  const sell = page.getByRole("button", { name: "Sell YES shares" });
  await expect(sell).toBeEnabled();
  await sell.click();
  await expect(page.getByText(/^Sold .+ YES shares/).first()).toBeVisible();

  // Below 768 px the ticket is a sheet over the page, and the tabs are behind it.
  const sheet = page.getByRole("dialog");
  if (await sheet.isVisible()) {
    await sheet.getByRole("button", { name: "Close" }).click();
    await expect(sheet).toBeHidden();
  }
  await stay();

  // Wallet, Leaderboard, Portfolio, Markets, the same market again.
  await tab("/wallet", "Wallet");
  await tab("/leaderboard", "Leaderboard");
  await expect(page.locator("img[data-brand]").first()).toBeVisible();
  await tab("/portfolio", "Portfolio");
  await tab("/", "Markets");
  await openMarket();

  // What the walk asked of the server, by address: a fetch and a revalidation answered
  // 304 both transfer something; what the browser took from its cache transfers nothing.
  const { shown, late, transfers } = await page.evaluate(() => {
    const report = (window as unknown as { brandReport: BrandReport }).brandReport;
    const timings = performance.getEntriesByType("resource") as PerformanceResourceTiming[];
    return {
      shown: report.shown,
      late: report.late,
      transfers: timings.filter((entry) => entry.transferSize > 0).map((entry) => entry.name),
    };
  });
  const asked = (address: string) => transfers.filter((name) => name === address).length;
  const askedByName = Object.fromEntries(
    Object.entries(shown).map(([name, addresses]) => [
      name,
      addresses.reduce((sum, address) => sum + asked(address), 0),
    ]),
  );

  // The walk did load the two images every screen shows.
  expect(askedByName).toMatchObject({ token: 1, "logo-lockup": 1 });
  // No brand image has two addresses, and none was asked for a second time.
  expect(Object.entries(shown).filter(([, addresses]) => addresses.length !== 1)).toEqual([]);
  expect(Object.entries(askedByName).filter(([, times]) => times > 1)).toEqual([]);
  // One that had loaded was in its element at the first frame, wherever it mounted.
  expect(late).toEqual([]);

  // The two screens of the walk that show the most brand images. They come after the
  // count, so that a screen whose layout does not hold cannot hide what the walk asked.
  await expectLayoutHolds(page);
  await screenshot(page, testInfo, "market");
  await tab("/leaderboard", "Leaderboard");
  await expect(page.locator("img[data-brand]").first()).toBeVisible();
  await expectLayoutHolds(page);
  await screenshot(page, testInfo, "leaderboard");
});
