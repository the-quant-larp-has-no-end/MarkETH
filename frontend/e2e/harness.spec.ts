import { BASE_URL, expect, expectLayoutHolds, screenshot, STUDENTS, test } from "./support";

/**
 * The harness itself: a person of the seed opens the app and is on Markets, at the width
 * of the project that runs. What every other spec builds on is in here once: the stack
 * takes the browser context for the person, the screen has the project's width, the
 * layout holds, and the screenshot lies where the runner looks for it.
 */
test("a seeded student sees Markets", async ({ people }, testInfo) => {
  const anna = await people.as(STUDENTS.anna);

  // The stack knows who this context is: the profile the seed made.
  const me = await anna.request.get(`${BASE_URL}/api/me`);
  expect(me.status(), await me.text()).toBe(200);
  expect(await me.json()).toMatchObject({ email: STUDENTS.anna.id, username: "anna_demo" });

  const page = await anna.newPage();
  expect(page.viewportSize()).toEqual(testInfo.project.use.viewport);
  await page.goto("/");

  await expect(page.getByRole("heading", { level: 1, name: "Markets" })).toBeAttached();
  await expect(
    page.getByRole("navigation", { name: "Main" }).locator('a[aria-current="page"]'),
  ).toHaveText("Markets");
  const cards = page.locator('[data-slot="market-list"] > li');
  await expect(cards.first()).toBeVisible();
  await expect(cards.first().getByRole("link").first()).toHaveAttribute("href", /^\/markets\//);

  await expectLayoutHolds(page);
  await screenshot(page, testInfo, "markets");
});
