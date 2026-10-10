import {
  BASE_URL,
  expect,
  expectLayoutHolds,
  type Newcomer,
  type Person,
  RUN,
  screenshot,
  STUDENTS,
  test,
} from "./support";

/**
 * The pickers of DESIGN.md §12: degree level and programme are chosen by typing, in the
 * profile set-up and in the filter sheet of the Leaderboard. Below 768 px a picker is a
 * button that opens a sheet with a search field; from 768 px it is a combobox in place.
 *
 * The people who set up a profile here are made up by this spec, one per test and
 * project, and nobody else's profile is written.
 */

type Page = Parameters<typeof expectLayoutHolds>[0];
type TestInfo = Parameters<typeof screenshot>[1];
type Locator = ReturnType<Page["getByRole"]>;
type BrowserContext = Newcomer["context"];

type Degrees = { level: string; name: string; programmes: string[] }[];

/** A person nobody has seen and who has no profile yet: the set-up screen is theirs to fill in. */
function newcomer(testInfo: TestInfo, n: number): Person & { username: string } {
  const project = testInfo.project.name === "phone" ? "p" : "d";
  return {
    id: `e2e.${RUN}.pk${project}${n}@ethz.ch`,
    name: `E2E Picker ${project}${n}`,
    username: `pk_${RUN}_${project}${n}`,
  };
}

const isPhone = (page: Page) => (page.viewportSize()?.width ?? 0) < 768;

/** The field of a picker: the button below 768 px, the combobox from there. */
function field(scope: Page | Locator, page: Page, label: string): Locator {
  return isPhone(page)
    ? scope.getByRole("button", { name: new RegExp(`^${label}: `) })
    : scope.getByRole("combobox", { name: label, exact: true });
}

/** The list of a picker once it is open: in its own sheet below 768 px, under the field from there. */
function listOf(scope: Page | Locator, page: Page, label: string): Locator {
  return isPhone(page)
    ? page.getByRole("dialog", { name: label, exact: true })
    : scope.getByRole("listbox", { name: label, exact: true });
}

/** Opens a picker and types into it, as a person at this width does. */
async function typeInto(scope: Page | Locator, page: Page, label: string, typed: string) {
  await field(scope, page, label).click();
  const list = listOf(scope, page, label);
  await expect(list).toBeVisible();
  const input = isPhone(page) ? list.getByRole("searchbox") : field(scope, page, label);
  await input.pressSequentially(typed);
  return list;
}

/** Chooses by typing some letters and taking the option that is meant. */
async function choose(
  scope: Page | Locator,
  page: Page,
  label: string,
  typed: string,
  option: string,
) {
  const list = await typeInto(scope, page, label, typed);
  await list.getByRole("option", { name: option, exact: true }).click();
  await expect(list).toBeHidden();
  await shows(scope, page, label, option);
}

/** The field says what is chosen. */
async function shows(scope: Page | Locator, page: Page, label: string, option: string) {
  if (isPhone(page)) {
    await expect(
      scope.getByRole("button", { name: `${label}: ${option}`, exact: true }),
    ).toBeVisible();
  } else {
    await expect(field(scope, page, label)).toHaveValue(option);
  }
}

async function json<T>(context: BrowserContext, path: string): Promise<T> {
  const answer = await context.request.get(`${BASE_URL}${path}`);
  expect(answer.status(), `GET ${path}: ${await answer.text()}`).toBe(200);
  return (await answer.json()) as T;
}

/**
 * The layout with a picker's list open. Below 768 px the list has a sheet of its own. From
 * 768 px it lies over what follows the field, by design, so it is measured by itself: its
 * options stand one under the other.
 */
async function expectLayoutHoldsAround(page: Page, list: Locator, ignore: readonly string[] = []) {
  await expectLayoutHolds(page, {
    ignore: isPhone(page) ? ignore : [...ignore, '[data-slot="picker-list"]'],
  });
  const boxes = await list.getByRole("option").evaluateAll((options) =>
    options.map((option) => {
      const box = option.getBoundingClientRect();
      return { top: box.top, bottom: box.bottom };
    }),
  );
  for (let at = 1; at < boxes.length; at += 1) {
    expect(boxes[at].top, `option ${at + 1} lies over option ${at}`).toBeGreaterThanOrEqual(
      boxes[at - 1].bottom - 1,
    );
  }
}

/** Presses Tab until `target` has the focus. */
async function tabTo(page: Page, target: Locator) {
  for (let presses = 0; presses < 12; presses += 1) {
    await page.keyboard.press("Tab");
    if (await target.evaluate((element) => element === document.activeElement)) return;
  }
  throw new Error(`The Tab key did not reach ${target}.`);
}

test('a new person types "bach" and "math", and the profile is created with BSc Mathematics', async ({
  people,
}, testInfo) => {
  const person = newcomer(testInfo, 1);
  const context = await people.as(person);
  const page = await context.newPage();
  await page.goto("/");

  await page.getByLabel("Username").fill(person.username);
  // The programme waits for its level.
  await expect(field(page, page, "Programme")).toBeDisabled();
  await choose(page, page, "Level", "bach", "Bachelor");

  const list = await typeInto(page, page, "Programme", "math");
  await expect(list.getByRole("option")).toHaveText(["BSc Mathematics"]);
  await expectLayoutHoldsAround(page, list);
  await screenshot(page, testInfo, "programme-typed");
  await list.getByRole("option", { name: "BSc Mathematics", exact: true }).click();
  await expect(list).toBeHidden();
  await shows(page, page, "Programme", "BSc Mathematics");

  await page.getByRole("checkbox", { name: "I accept the usage policy" }).check();
  await expectLayoutHolds(page);
  await screenshot(page, testInfo, "setup-filled");
  await page.getByRole("button", { name: "Create profile" }).click();

  await expect(page.getByRole("button", { name: "Create profile" })).toBeHidden();
  expect(await json<Record<string, unknown>>(context, "/api/me")).toMatchObject({
    username: person.username,
    degree_level: "BACHELOR",
    degree_programme: "BSc Mathematics",
  });

  // The Profile screen holds the same two pickers, with what was chosen.
  await page.goto("/profile");
  await shows(page, page, "Level", "Bachelor");
  await shows(page, page, "Programme", "BSc Mathematics");
  await expectLayoutHolds(page);
  await screenshot(page, testInfo, "profile");
});

test("the same with the keyboard alone, from 768 px", async ({ people }, testInfo) => {
  test.skip(
    testInfo.project.name !== "desktop",
    "Below 768 px a picker is a sheet that is tapped; the walk by keyboard is the combobox's.",
  );
  const person = newcomer(testInfo, 2);
  const context = await people.as(person);
  const page = await context.newPage();
  await page.goto("/");
  await expect(page.getByLabel("Username")).toBeVisible();

  await tabTo(page, page.getByLabel("Username"));
  await page.keyboard.type(person.username);

  await page.keyboard.press("Tab");
  const level = field(page, page, "Level");
  await expect(level).toBeFocused();
  await page.keyboard.type("bach");
  await expect(listOf(page, page, "Level").getByRole("option")).toHaveText(["Bachelor"]);
  await page.keyboard.press("Enter");
  await expect(level).toHaveValue("Bachelor");
  await expect(level).toBeFocused();
  await expect(listOf(page, page, "Level")).toBeHidden();

  await page.keyboard.press("Tab");
  const programme = field(page, page, "Programme");
  await expect(programme).toBeFocused();
  await page.keyboard.type("math");
  await page.keyboard.press("Enter");
  await expect(programme).toHaveValue("BSc Mathematics");

  // Another look at the list by the arrows, and Escape leaves the choice as it is.
  await page.keyboard.press("ArrowDown");
  await expect(listOf(page, page, "Programme")).toBeVisible();
  await expect(listOf(page, page, "Programme").getByRole("option", { selected: true })).toHaveText(
    "BSc Mathematics",
  );
  await page.keyboard.press("Escape");
  await expect(listOf(page, page, "Programme")).toBeHidden();
  await expect(programme).toHaveValue("BSc Mathematics");

  await tabTo(page, page.getByRole("checkbox", { name: "I accept the usage policy" }));
  await page.keyboard.press("Space");
  await tabTo(page, page.getByRole("button", { name: "Create profile" }));
  await page.keyboard.press("Enter");

  await expect(page.getByRole("button", { name: "Create profile" })).toBeHidden();
  expect(await json<Record<string, unknown>>(context, "/api/me")).toMatchObject({
    username: person.username,
    degree_level: "BACHELOR",
    degree_programme: "BSc Mathematics",
  });
});

test("the filter sheet narrows the Leaderboard to a picked programme, and its chip names it", async ({
  people,
}, testInfo) => {
  const anna = await people.as(STUDENTS.anna);
  const me = await json<{ degree_level: string; degree_programme: string }>(anna, "/api/me");
  const { degrees } = await json<{ degrees: Degrees }>(anna, "/api/config");
  const level = degrees.find((degree) => degree.level === me.degree_level);
  if (level === undefined) throw new Error(`GET /api/config has no level ${me.degree_level}.`);

  // What the API lists for Anna's programme, and who is on the board without it.
  const filter = new URLSearchParams({
    degree_level: me.degree_level,
    degree_programme: me.degree_programme,
  });
  type Board = { items: { username: string }[] };
  const inside = (await json<Board>(anna, `/api/leaderboard?${filter}`)).items.map(
    (entry) => entry.username,
  );
  const outside = (await json<Board>(anna, "/api/leaderboard")).items
    .map((entry) => entry.username)
    .filter((username) => !inside.includes(username));

  const page = await anna.newPage();
  await page.goto("/leaderboard");
  await page.getByRole("button", { name: /^Filter/ }).click();
  const sheet = page.getByRole("dialog", { name: "Filter", exact: true });
  await expect(sheet).toBeVisible();
  // The line under the title is a live region, which stays in the accessibility tree
  // behind a sheet: the layout check would take it for a piece the sheet lies over.
  const behindTheSheet = ['[data-slot="measure-line"]'];

  await shows(sheet, page, "Level", "Any level");
  await expect(field(sheet, page, "Programme")).toBeDisabled();
  await choose(sheet, page, "Level", level.name.slice(0, 4).toLowerCase(), level.name);
  // The picker's list is gone and the filter sheet is still there.
  await expect(sheet).toBeVisible();

  const list = await typeInto(sheet, page, "Programme", me.degree_programme.toLowerCase());
  await expectLayoutHoldsAround(page, list, behindTheSheet);
  await screenshot(page, testInfo, "filter-programme-typed");
  await list.getByRole("option", { name: me.degree_programme, exact: true }).click();
  await expect(list).toBeHidden();
  await shows(sheet, page, "Programme", me.degree_programme);
  await expectLayoutHolds(page, { ignore: behindTheSheet });
  await screenshot(page, testInfo, "filter-picked");

  const asked = page.waitForRequest(
    (request) =>
      request.url().includes("/api/leaderboard?") &&
      new URL(request.url()).searchParams.get("degree_programme") === me.degree_programme,
  );
  await sheet.getByRole("button", { name: "Show results" }).click();
  expect(new URL((await asked).url()).searchParams.get("degree_level")).toBe(me.degree_level);
  await expect(sheet).toBeHidden();

  await expect(page).toHaveURL(
    (address) =>
      address.searchParams.get("degree_level") === me.degree_level &&
      address.searchParams.get("degree_programme") === me.degree_programme,
  );
  const chips = page.getByRole("list", { name: "Filters that are set" });
  await expect(chips.getByRole("button")).toHaveText([me.degree_programme]);
  await expect(
    chips.getByRole("button", { name: `Remove filter: ${me.degree_programme}`, exact: true }),
  ).toBeVisible();

  for (const username of inside) {
    await expect(page.getByText(username, { exact: true }).first()).toBeVisible();
  }
  for (const username of outside) {
    await expect(page.getByText(username, { exact: true })).toHaveCount(0);
  }
  await expectLayoutHolds(page);
  await screenshot(page, testInfo, "leaderboard-narrowed");
});

test("the longest label of the catalogue wraps inside its option, and nothing lies over anything else", async ({
  people,
}, testInfo) => {
  // Nothing is saved: the person looks at the set-up screen and leaves.
  const context = await people.as(newcomer(testInfo, 3));
  const { degrees } = await json<{ degrees: Degrees }>(context, "/api/config");
  const longest = degrees
    .flatMap((degree) => degree.programmes.map((label) => ({ level: degree.name, label })))
    .reduce((found, next) => (next.label.length > found.label.length ? next : found));
  expect(longest.label.length).toBeGreaterThanOrEqual(60);

  const page = await context.newPage();
  await page.goto("/");
  await choose(page, page, "Level", longest.level.slice(0, 5).toLowerCase(), longest.level);

  // Its first and its last word, typed: the two ends of the label are what has to fit.
  const words = longest.label.split(" ");
  const typed = `${words[0]} ${words[words.length - 1]}`.toLowerCase();
  const list = await typeInto(page, page, "Programme", typed);
  const option = list.getByRole("option", { name: longest.label, exact: true });
  await expect(option).toBeVisible();

  const shape = await option.evaluate((element) => {
    const text = element.querySelector("span") ?? element;
    const listbox = element.closest('[role="listbox"]') ?? element;
    const frame = (listbox.parentElement ?? listbox).getBoundingClientRect();
    const box = element.getBoundingClientRect();
    // The lines the label stands on: the boxes of its text, by their tops.
    const letters = document.createRange();
    letters.selectNodeContents(text);
    const lines = new Set(Array.from(letters.getClientRects(), (line) => Math.round(line.top)));
    return {
      lines: lines.size,
      cut: text.scrollWidth > text.clientWidth + 1,
      insideLeft: box.left >= frame.left - 1,
      insideRight: box.right <= frame.right + 1,
    };
  });
  expect(shape, longest.label).toMatchObject({ cut: false, insideLeft: true, insideRight: true });
  expect(shape.lines, `"${longest.label}" stands on one line`).toBeGreaterThanOrEqual(2);
  await expectLayoutHoldsAround(page, list);
  await screenshot(page, testInfo, "longest-option");

  await option.click();
  await expect(list).toBeHidden();
  await shows(page, page, "Programme", longest.label);
  // In the field the label keeps to one line and to the field's width.
  await expectLayoutHolds(page);
  await screenshot(page, testInfo, "longest-chosen");
});
