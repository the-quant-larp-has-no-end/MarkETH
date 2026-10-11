import {
  BASE_URL,
  expect,
  expectLayoutHolds,
  type LayoutOptions,
  type Newcomer,
  type People,
  type Person,
  RUN,
  screenshot,
  test,
} from "./support";

/**
 * The live stream of a market (DESIGN.md §13: "Live stream", "Live stream studio", "Live
 * now" on Markets and the block "Live stream" of the admin area): a browser records
 * Chrome's stand-in camera in the studio and sends the video up in pieces, the stack makes
 * a stream of it that plays a few seconds behind, and another person watches it on the
 * market's page.
 *
 * The markets of this spec, their proposers and their viewers are made up by it. The one
 * person it does not make up is the admin (`ADMIN`), who acts on those markets alone. A
 * walk takes back what it leaves: a live stream that is still on is ended, and its market
 * is closed and voided, so a run leaves nothing on air and no market open. The stack lets
 * few live streams run at once (`BROADCAST_MAX_LIVE`): one that somebody else has on
 * while the spec runs is refused with the API's sentence.
 */

// Chrome's stand-in camera and microphone, handed over without a question: a moving test
// picture with a tone, which is what the studio records here.
test.use({
  launchOptions: {
    args: ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"],
  },
});

type Page = Parameters<typeof expectLayoutHolds>[0];
type TestInfo = Parameters<typeof screenshot>[1];
type Locator = ReturnType<Page["getByRole"]>;
type BrowserContext = Newcomer["context"];

type Market = { id: string; question: string };
type MarketBroadcast = {
  broadcast: { id: string; status: string; playlist_url: string | null } | null;
};

/**
 * The bootstrap admin of `.example.env` (`ADMIN_EMAILS`): the one admin a stack has before
 * anybody was given a role, and the one the stack of a gate is started with. The seed
 * registers nobody under this address, so on a stack where nobody has signed in as it, it
 * sets up its profile first.
 */
const ADMIN: Person = { id: "admin@ethz.ch", name: "Ada Admin" };
const ADMIN_USERNAME = "e2e_admin";

/** The API takes a request that changes something only from its own pages (SPEC A.2). */
const FROM_THE_APP = { Origin: new URL(BASE_URL).origin };

const DAY_MS = 24 * 60 * 60 * 1000;

/** A whole walk: two or three people, a camera, and a stream that plays seconds behind. */
const WALK_MS = 240_000;
/** Until the stand-in camera shows a picture in the preview. */
const CAMERA_MS = 20_000;
/** Until a live stream is on air: the recorder's first pieces, and the first segments made of them. */
const ON_AIR_MS = 45_000;
/** Until an open page follows what happened elsewhere: an event, or the poll that stands in for one. */
const FOLLOWS_MS = 20_000;
/** Until the proxy has asked again whether a viewer may watch (`BROADCAST_ACCESS_CACHE_SECONDS`). */
const ASKED_AGAIN_MS = 30_000;
/** A cut of the broadcaster's connection that a viewer does not notice: less than the player holds. */
const SHORT_CUT_MS = 5_000;
/** How long after a short cut the picture is still watched for a waiting line. */
const AFTER_A_CUT_MS = 10_000;
/** A cut that outlasts what the player holds, and stays well under the stall rule (`stall_seconds`). */
const LONG_CUT_MS = 25_000;
/**
 * How long a picture goes without a waiting line to count as steady. While a playlist
 * stands still, each player the box builds plays its last segments once more and waits
 * again, well inside this time.
 */
const STEADY_MS = 15_000;
/** Until the picture is steady after a long cut: the pieces that waited, and a player or two. */
const STEADY_AGAIN_MS = 120_000;
/** The walk with the two cuts: the cuts themselves, and what each takes to be over. */
const CUTS_MS = 360_000;

/**
 * Market detail pins its two outcome buttons below 768 px once their place is out of view,
 * and the bar takes its place at the end of the page. The layout check scrolls the page to
 * its end, the bar is mounted by that scroll and the page grows under it, so the check
 * would measure the bar a frame before it stands where it belongs. It is left out there;
 * `expectOnScreenTogether` measures it where a viewer has it.
 */
const ON_MARKET_DETAIL: LayoutOptions = { ignore: ['[data-slot="pinned"]'] };

const isPhone = (page: Page) => (page.viewportSize()?.width ?? 0) < 768;

/** `GET` of a path of the stack as the person of `context`: the body of its answer. */
async function read<Body>(context: BrowserContext, path: string): Promise<Body> {
  const answer = await context.request.get(`${BASE_URL}${path}`);
  expect(answer.status(), `GET ${path}: ${await answer.text()}`).toBe(200);
  return (await answer.json()) as Body;
}

/** `POST` to a path of the stack as the person of `context`: the body of its answer. */
async function write<Body>(context: BrowserContext, path: string, body?: unknown): Promise<Body> {
  const answer = await context.request.post(`${BASE_URL}${path}`, {
    headers: FROM_THE_APP,
    data: body,
  });
  expect(answer.ok(), `POST ${path} answered ${answer.status()}: ${await answer.text()}`).toBe(
    true,
  );
  return (await answer.json()) as Body;
}

/**
 * A browser context as the bootstrap admin, with a profile: the API lets nobody act
 * without one. Fails when the stack does not take the address for an admin.
 */
async function asAdmin(people: People): Promise<BrowserContext> {
  const context = await people.as(ADMIN);
  let me = await context.request.get(`${BASE_URL}/api/me`);
  if (me.status() === 403 && (await me.json()).code === "PROFILE_REQUIRED") {
    type Degrees = { level: string; programmes: string[] }[];
    const { degrees } = await read<{ degrees: Degrees }>(context, "/api/config");
    const level = degrees.find((option) => option.programmes.length > 0);
    if (level === undefined) throw new Error("GET /api/config offers no degree programme.");
    const saved = await context.request.put(`${BASE_URL}/api/me/profile`, {
      headers: FROM_THE_APP,
      data: {
        username: ADMIN_USERNAME,
        degree_level: level.level,
        degree_programme: level.programmes[0],
        policy_accepted: true,
      },
    });
    expect(saved.ok(), `PUT /api/me/profile as ${ADMIN.id}: ${await saved.text()}`).toBe(true);
    me = await context.request.get(`${BASE_URL}/api/me`);
  }
  expect(me.status(), `GET /api/me as ${ADMIN.id}: ${await me.text()}`).toBe(200);
  const { role }: { role: string } = await me.json();
  expect(
    role,
    `${ADMIN.id} is no admin on this stack. Its ADMIN_EMAILS names another one: the specs act as the one of .example.env.`,
  ).toBe("ADMIN");
  return context;
}

/** A question nobody has asked: it names the run and the project that asks it. */
function questionOf(testInfo: TestInfo, question: string): string {
  return `${question} (${RUN} ${testInfo.project.name})`;
}

/**
 * A market that is open for trading: `proposer` proposes it and the admin opens it, as on
 * the screens. It closes in a week.
 */
async function openMarket(
  proposer: BrowserContext,
  admin: BrowserContext,
  question: string,
): Promise<Market> {
  const closeAt = new Date(Date.now() + 7 * DAY_MS);
  const proposal = await write<Market>(proposer, "/api/markets", {
    question,
    yes_definition: "The browser suite says so.",
    no_definition: "The browser suite says otherwise.",
    resolution_criteria: "A market of the browser suite. The run that made it voids it.",
    resolution_source: "The run of the browser suite that made it.",
    close_at: closeAt.toISOString(),
    resolve_by: new Date(closeAt.getTime() + DAY_MS).toISOString(),
    policy_accepted: true,
  });
  return write<Market>(admin, `/api/admin/markets/${proposal.id}/approve`, { tier: "TIGHT" });
}

/** The live stream of a market, as the API shows it to the person of `context`. */
function broadcastOf(context: BrowserContext, marketId: string): Promise<MarketBroadcast> {
  return read<MarketBroadcast>(context, `/api/markets/${marketId}/broadcast`);
}

/**
 * Takes back what a walk leaves of its market: a live stream that is still on is ended,
 * and the market is closed and voided. What goes wrong here is reported beside what the
 * walk found and never in its place.
 */
async function takeBack(admin: BrowserContext, marketId: string): Promise<void> {
  try {
    const { broadcast } = await broadcastOf(admin, marketId);
    if (broadcast?.status === "LIVE") await write(admin, `/api/broadcasts/${broadcast.id}/end`);
    await write(admin, `/api/admin/markets/${marketId}/close`, {
      note: "The browser suite is done with it.",
    });
    await write(admin, `/api/admin/markets/${marketId}/void`, {
      reason: "A market of the browser suite.",
    });
  } catch (problem) {
    expect.soft(problem, `taking back the market ${marketId}`).toBeUndefined();
  }
}

/**
 * The looks of one walk: at each screen the layout is measured and the screenshot taken
 * where the walk stands, and what does not hold is said when the walk is over. A screen
 * whose layout does not hold then hides nothing the walk proves after it, and what the
 * walk still proves after the looks are told is reported beside them.
 */
function looksOf(testInfo: TestInfo) {
  const problems: string[] = [];
  return {
    async at(page: Page, screen: string, options?: LayoutOptions): Promise<void> {
      await expectLayoutHolds(page, options).catch((problem: Error) => {
        problems.push(`${screen}: ${problem.message}`);
      });
      await screenshot(page, testInfo, screen);
    },
    expectAllHold(): void {
      expect.soft(problems, "screens whose layout does not hold").toEqual([]);
    },
  };
}

/** What the console of `page` says about its Content-Security-Policy, from now on. */
function policyViolationsOf(page: Page): string[] {
  const said: string[] = [];
  page.on("console", (message) => {
    if (/Content Security Policy/i.test(message.text())) said.push(message.text());
  });
  return said;
}

/** Each of `parts` is on screen whole, and each stands under the one before it. */
async function expectOnScreenTogether(
  page: Page,
  parts: readonly { name: string; part: Locator }[],
): Promise<void> {
  const screenHeight = page.viewportSize()?.height ?? 0;
  let above = { name: "the upper edge of the screen", end: 0 };
  for (const { name, part } of parts) {
    await expect(part, name).toBeVisible();
    const box = await part.boundingBox();
    if (box === null) throw new Error(`${name} has no place on the page.`);
    expect.soft(box.y, `${name} begins under ${above.name}`).toBeGreaterThanOrEqual(above.end - 1);
    expect
      .soft(box.y + box.height, `${name} ends above the lower edge of the screen`)
      .toBeLessThanOrEqual(screenHeight + 1);
    above = { name, end: box.y + box.height };
  }
}

/** The picture of a video box plays, and its position advances. */
async function expectPictureAdvances(box: Locator): Promise<void> {
  await expect(box).toHaveAttribute("data-phase", "playing", { timeout: ON_AIR_MS });
  const picture = box.locator("video");
  const position = () => picture.evaluate((element) => (element as HTMLVideoElement).currentTime);
  const from = await position();
  await expect.poll(position, { timeout: FOLLOWS_MS }).toBeGreaterThan(from + 2);
}

/** What a video box says about its picture, read four times a second for `ms`. */
async function phasesDuring(page: Page, box: Locator, ms: number): Promise<string[]> {
  const seen: string[] = [];
  const until = Date.now() + ms;
  while (Date.now() < until) {
    seen.push((await box.getAttribute("data-phase", { timeout: FOLLOWS_MS })) ?? "none");
    await page.waitForTimeout(250);
  }
  return seen;
}

/** The last segment a playlist names, as the proxy serves it to the person of `context`. */
async function lastSegmentOf(context: BrowserContext, playlist: string): Promise<string> {
  const answer = await context.request.get(`${BASE_URL}${playlist}`);
  expect(answer.status(), `GET ${playlist}`).toBe(200);
  const named = (await answer.text())
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line !== "" && !line.startsWith("#"));
  return named.at(-1) ?? "";
}

/** The studio goes live: the preview shows the stand-in camera, and "Go live" starts the stream. */
async function goLive(studio: Page): Promise<void> {
  await expect(studio.locator('video[aria-label="Preview"]')).toBeVisible({ timeout: CAMERA_MS });
  // The button waits for the picture: nobody goes live with a camera that shows nothing.
  const button = studio.getByRole("button", { name: "Go live", exact: true });
  await expect(button).toBeEnabled({ timeout: CAMERA_MS });
  await button.click();
  await expect(studio.getByRole("heading", { name: "You are live" })).toBeVisible({
    timeout: FOLLOWS_MS,
  });
}

/** "End stream" in the studio, and the dialog that asks for it a second time. */
async function endStream(studio: Page): Promise<void> {
  await studio.getByRole("button", { name: "End stream", exact: true }).click();
  const dialog = studio.getByRole("alertdialog", { name: "End the live stream?" });
  await dialog.getByRole("button", { name: "End stream", exact: true }).click();
  await expect(studio.getByRole("heading", { name: "The live stream has ended." })).toBeVisible({
    timeout: FOLLOWS_MS,
  });
}

test("an admin goes live from the camera, a second person watches, and the end reaches them", async ({
  people,
}, testInfo) => {
  test.setTimeout(WALK_MS);
  const looks = looksOf(testInfo);
  const admin = await asAdmin(people);
  const proposer = await people.join();
  const viewer = await people.join();
  const market = await openMarket(
    proposer.context,
    admin,
    questionOf(testInfo, "Does the camera of the browser suite go live?"),
  );

  try {
    // The second person is on Markets before anything is on air.
    const page = await viewer.context.newPage();
    const violations = policyViolationsOf(page);
    await page.goto("/");
    await expect(page.locator('[data-slot="market-list"] > li').first()).toBeVisible();
    const liveNow = page.getByRole("link", { name: `Live now: ${market.question}`, exact: true });
    await expect(liveNow).toHaveCount(0);

    // The admin opens the studio of the market and goes live.
    const studio = await admin.newPage();
    const studioViolations = policyViolationsOf(studio);
    await studio.goto(`/markets/${market.id}/live`);
    await expect(studio.locator('video[aria-label="Preview"]')).toBeVisible({
      timeout: CAMERA_MS,
    });
    await looks.at(studio, "studio-set-up");
    await goLive(studio);
    const wentLive = Date.now();
    // Leaving through the app and closing the tab both ask before the camera is lost.
    const leavingViaLink = studio.waitForEvent("dialog", { timeout: FOLLOWS_MS });
    const profileClick = studio.getByRole("link", { name: "Profile", exact: true }).click();
    const leaveDialog = await leavingViaLink;
    expect(leaveDialog.type()).toBe("confirm");
    expect(leaveDialog.message()).toContain("Leaving this page stops your broadcast.");
    await leaveDialog.dismiss();
    await profileClick;
    await expect(studio).toHaveURL(new RegExp(`/markets/${market.id}/live$`));
    await expect(studio.getByRole("heading", { name: "You are live" })).toBeVisible();

    const leavingViaClose = studio.waitForEvent("dialog", { timeout: FOLLOWS_MS });
    const close = studio.close({ runBeforeUnload: true });
    const closeDialog = await leavingViaClose;
    expect(closeDialog.type()).toBe("beforeunload");
    await closeDialog.dismiss();
    await close;
    await expect(studio.getByRole("heading", { name: "You are live" })).toBeVisible();
    const studioChat = studio.getByRole("region", { name: "Live chat" });
    await expect(studioChat).toHaveAttribute("data-state", "open");
    await expect(studio.getByRole("button", { name: "End stream", exact: true })).toBeVisible();
    if (isPhone(studio)) {
      await studio.evaluate(() => window.scrollTo(0, 0));
      await expectOnScreenTogether(studio, [
        { name: "the preview", part: studio.locator('video[aria-label="Preview"]') },
        {
          name: "the stream controls",
          part: studio.getByRole("button", { name: "End stream", exact: true }),
        },
        { name: "the chat composer", part: studioChat.getByRole("textbox", { name: "Message" }) },
      ]);
    }
    await looks.at(studio, "studio-live");

    // "Live now" reaches the page that is open, without a reload.
    await expect(liveNow).toBeVisible({ timeout: ON_AIR_MS });
    testInfo.annotations.push({
      type: "Live now on Markets",
      description: `${Date.now() - wentLive} ms after "You are live"`,
    });
    await looks.at(page, "markets-live-now");

    // The row leads to the market, and the video starts with a tap and not before.
    await liveNow.click();
    await expect(page).toHaveURL(new RegExp(`/markets/${market.id}$`));
    const section = page.locator('section[data-slot="live-stream"]');
    const box = page.locator('[data-slot="video-box"]');
    await expect(section.getByRole("button", { name: "Watch live" })).toBeVisible({
      timeout: ON_AIR_MS,
    });
    await expect(box).toHaveCount(0);
    await section.getByRole("button", { name: "Watch live" }).click();

    // The picture arrives and advances.
    await expectPictureAdvances(box);
    const picture = box.locator("video");
    await expect(picture).toHaveAttribute("aria-label", `Live stream: ${market.question}`);
    expect(
      await picture.evaluate((element) => (element as HTMLVideoElement).videoWidth),
      "the width of the picture the element shows",
    ).toBeGreaterThan(0);

    // The chat stands under the picture while the stream is on.
    const chat = page.getByRole("region", { name: "Live chat" });
    await expect(chat.getByRole("textbox", { name: "Message" })).toBeVisible();

    if (isPhone(page)) {
      // Below 768 px the tab bar is out of the way, and the picture, the composer of the
      // chat and the two outcome buttons are on screen together.
      await expect(page.getByRole("navigation", { name: "Main" })).toBeHidden();
      await expect(page.locator('[data-slot="live-stream-box"]')).toBeVisible();
      await expectOnScreenTogether(page, [
        { name: "the picture", part: box },
        { name: "the composer of the chat", part: chat.getByRole("textbox", { name: "Message" }) },
        { name: "the outcome buttons", part: page.getByRole("button", { name: /^YES / }) },
      ]);
    }
    await looks.at(page, "market-watching", ON_MARKET_DETAIL);

    // The broadcaster reads and writes in the same chat without leaving the camera's page.
    const viewerMessage = "Can you see this in the studio?";
    await chat.getByRole("textbox", { name: "Message" }).fill(viewerMessage);
    await chat.getByRole("button", { name: "Send", exact: true }).click();
    await expect(studioChat.getByRole("log")).toContainText(viewerMessage, { timeout: FOLLOWS_MS });
    const broadcasterMessage = "Yes, welcome to the stream!";
    await studioChat.getByRole("textbox", { name: "Message" }).fill(broadcasterMessage);
    await studioChat.getByRole("button", { name: "Send", exact: true }).click();
    await expect(chat.getByRole("log")).toContainText(broadcasterMessage, { timeout: FOLLOWS_MS });
    await expect(studio.getByRole("heading", { name: "You are live" })).toBeVisible();
    await looks.at(studio, "studio-live-chat");

    const { broadcast } = await broadcastOf(viewer.context, market.id);
    const playlist = broadcast?.playlist_url;
    if (typeof playlist !== "string") {
      throw new Error("The live stream that plays has no playlist in the API's answer.");
    }

    // "End stream" in the studio ends it for the viewer, who taps nothing.
    await endStream(studio);
    await expect(studioChat).toHaveCount(0);
    await looks.at(studio, "studio-ended");
    await expect(section.getByText("The live stream has ended.", { exact: true })).toBeVisible({
      timeout: FOLLOWS_MS,
    });
    await expect(box).toHaveCount(0);
    await expect(chat).toHaveCount(0);
    if (isPhone(page)) {
      await expect(page.getByRole("navigation", { name: "Main" })).toBeVisible();
    }
    await looks.at(page, "market-ended", ON_MARKET_DETAIL);

    // Neither page broke the Content-Security-Policy of the stack on the way.
    expect.soft(violations, "what the viewer's console says about the policy").toEqual([]);
    expect.soft(studioViolations, "what the studio's console says about the policy").toEqual([]);
    looks.expectAllHold();

    // Nothing is on air, and the proxy hands the video of a stream that is over to nobody.
    const { items } = await read<{ items: { market_id: string }[] }>(
      viewer.context,
      "/api/broadcasts/live",
    );
    expect(items.map((item) => item.market_id)).not.toContain(market.id);
    await expect
      .poll(async () => (await viewer.context.request.get(`${BASE_URL}${playlist}`)).status(), {
        timeout: ASKED_AGAIN_MS,
      })
      .toBe(403);
  } finally {
    await takeBack(admin, market.id);
  }
});

test("the market's proposer asks for a live stream, an admin approves, and the proposer goes live", async ({
  people,
}, testInfo) => {
  test.setTimeout(WALK_MS);
  const looks = looksOf(testInfo);
  const admin = await asAdmin(people);
  const proposer = await people.join();
  const market = await openMarket(
    proposer.context,
    admin,
    questionOf(testInfo, "Does the proposer of this market go live?"),
  );

  try {
    // The proposer asks on the page of their market.
    const page = await proposer.context.newPage();
    await page.goto(`/markets/${market.id}`);
    const section = page.locator('section[data-slot="live-stream"]');
    const form = section.locator('[data-slot="live-stream-request"]');
    const ask = form.getByRole("button", { name: "Request a live stream" });
    await expect(ask).toBeDisabled();
    await form.getByRole("checkbox", { name: "I accept the usage policy" }).check();
    await looks.at(page, "market-request", ON_MARKET_DETAIL);
    await ask.click();
    await expect(
      section.getByText("You asked for a live stream. VIS decides on it."),
    ).toBeVisible();
    await expect(form).toHaveCount(0);

    // An admin finds the request in the block "Live stream" of the market and approves it.
    const desk = await admin.newPage();
    await desk.goto(`/admin/markets/${market.id}`);
    const block = desk.locator('[data-slot="broadcast-panel"]');
    const says = block.locator('p[role="status"]');
    await expect(says).toContainText(`Requested by ${proposer.username} (${proposer.person.id})`);
    await looks.at(desk, "admin-requested");
    await block.getByRole("button", { name: "Approve", exact: true }).click();
    await expect(says).toContainText(`Approved. ${proposer.username} can go live.`);

    // The approval reaches the proposer's page without a reload, and leads to the studio.
    const setUp = section.getByRole("link", { name: "Set up the live stream" });
    await expect(setUp).toBeVisible({ timeout: FOLLOWS_MS });
    await looks.at(page, "market-approved", ON_MARKET_DETAIL);
    await setUp.click();
    await expect(page).toHaveURL(new RegExp(`/markets/${market.id}/live$`));
    await goLive(page);
    await looks.at(page, "studio-live");

    // The admin's block follows by itself, and holds the way to end it.
    await expect(says).toContainText("Live since", { timeout: FOLLOWS_MS });
    await expect(block.getByRole("button", { name: "End stream", exact: true })).toBeVisible();
    await looks.at(desk, "admin-live");

    // The proposer ends their own stream.
    await endStream(page);
    await expect(says).toContainText("The last live stream ended", { timeout: FOLLOWS_MS });
    expect((await broadcastOf(admin, market.id)).broadcast?.status).toBe("ENDED");

    looks.expectAllHold();
  } finally {
    await takeBack(admin, market.id);
  }
});

test("the broadcaster's connection is cut: a moment goes unnoticed, a longer cut shows the waiting line, and the picture comes back", async ({
  people,
}, testInfo) => {
  test.setTimeout(CUTS_MS);
  const admin = await asAdmin(people);
  const proposer = await people.join();
  const viewer = await people.join();
  const market = await openMarket(
    proposer.context,
    admin,
    questionOf(testInfo, "Does the live stream outlast a cut connection?"),
  );

  try {
    const studio = await admin.newPage();
    await studio.goto(`/markets/${market.id}/live`);
    await goLive(studio);
    const live = studio.getByRole("heading", { name: "You are live" });

    const page = await viewer.context.newPage();
    await page.goto(`/markets/${market.id}`);
    const section = page.locator('section[data-slot="live-stream"]');
    const watch = section.getByRole("button", { name: "Watch live" });
    await expect(watch).toBeVisible({ timeout: ON_AIR_MS });
    await watch.click();
    const box = page.locator('[data-slot="video-box"]');
    await expectPictureAdvances(box);
    const { broadcast } = await broadcastOf(viewer.context, market.id);
    const playlist = broadcast?.playlist_url;
    if (typeof playlist !== "string") {
      throw new Error("The live stream that plays has no playlist in the API's answer.");
    }

    // Five seconds without a connection: the studio keeps its pieces and sends them when
    // it is back, and the viewer, who plays seconds behind, is never told to wait.
    await admin.setOffline(true);
    const around = await phasesDuring(page, box, SHORT_CUT_MS);
    await admin.setOffline(false);
    around.push(...(await phasesDuring(page, box, AFTER_A_CUT_MS)));
    expect(around, "what the video box says around a cut of five seconds").not.toContain("waiting");
    await expectPictureAdvances(box);
    await expect(live).toBeVisible();

    // Twenty-five seconds outlast what the player holds: the waiting line, and no end.
    const cutAt = Date.now();
    await admin.setOffline(true);
    await expect(box).toHaveAttribute("data-phase", "waiting", { timeout: LONG_CUT_MS });
    await expect(box).toContainText("Waiting for the live stream");
    await screenshot(page, testInfo, "market-waiting");
    await page.waitForTimeout(Math.max(0, cutAt + LONG_CUT_MS - Date.now()));
    const stoodStill = await lastSegmentOf(viewer.context, playlist);
    await admin.setOffline(false);

    // The studio sends again by itself: the playlist moves on, and the picture is steady
    // again without a tap of anybody.
    await expect(async () => {
      expect(await lastSegmentOf(viewer.context, playlist)).not.toBe(stoodStill);
    }).toPass({ timeout: ON_AIR_MS });
    await expect(async () => {
      await expectPictureAdvances(box);
      expect(await phasesDuring(page, box, STEADY_MS)).not.toContain("waiting");
    }).toPass({ timeout: STEADY_AGAIN_MS });
    await expect(section.getByText("The live stream has ended.", { exact: true })).toHaveCount(0);
    await expect(live).toBeVisible();
  } finally {
    // What takes the market back asks as the admin, whose connection a failed walk may have left cut.
    await admin.setOffline(false);
    await takeBack(admin, market.id);
  }
});
