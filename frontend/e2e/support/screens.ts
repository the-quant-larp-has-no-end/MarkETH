import path from "node:path";

import type { Page, TestInfo } from "@playwright/test";

import { SCREENSHOTS } from "./runner";

/**
 * A screenshot of the screen `page` shows, for the review against DESIGN.md. It lies in
 * the directory the runner names with `E2E_SCREENSHOTS`, or in `screens/` of the run's
 * output directory, as `<spec>.<screen>.<project>.png`: `harness.markets.phone.png`.
 *
 * The picture is what a person sees at that width. `whole` takes the page to its end
 * instead, with whatever is pinned drawn where it stands before the first scroll.
 */
export async function screenshot(
  page: Page,
  testInfo: TestInfo,
  screen: string,
  { whole = false }: { whole?: boolean } = {},
): Promise<string> {
  const directory =
    SCREENSHOTS === undefined
      ? path.join(testInfo.project.outputDir, "screens")
      : path.resolve(SCREENSHOTS);
  const spec = path.basename(testInfo.file).replace(/\.spec\.ts$/, "");
  const file = path.join(directory, `${spec}.${screen}.${testInfo.project.name}.png`);
  await page.screenshot({ path: file, fullPage: whole, animations: "disabled" });
  return file;
}
