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
 * The live chat of a market (DESIGN.md §13, "Live chat"): the panel under the section
 * "Live stream" of Market detail. It is there exactly while the market has a live stream
 * that is on, and what is written in it goes with that stream.
 *
 * The live stream of this spec has no camera. The admin starts it with the request the
 * studio sends at "Go live" (`POST /admin/markets/{market_id}/broadcast`), and the chat is
 * open from that answer on; what a camera sends and what a viewer plays is
 * `broadcast.spec.ts`. A stream nobody sends video to ends by itself (`stall_seconds` of
 * `GET /config`), and the walk is done with each of its two streams long before that.
 *
 * The market, its proposer and the two people who write are made up by this spec. The one
 * person it does not make up is the admin (`ADMIN`), who acts on that market alone. The
 * walk takes back what it leaves: a live stream that is still on is ended, and the market
 * is closed and voided.
 */

type Page = Parameters<typeof expectLayoutHolds>[0];
type TestInfo = Parameters<typeof screenshot>[1];
type Locator = ReturnType<Page["getByRole"]>;
type BrowserContext = Newcomer["context"];

type Market = { id: string; question: string };
type MarketBroadcast = { broadcast: { id: string; status: string } | null };
type Chat = { open: boolean; broadcast_id: string | null; items: { text: string }[] };

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

/** A whole walk: up to four people, three pages and two live streams. */
const WALK_MS = 180_000;
/**
 * Until a message is on the screens of the others. It travels on the event stream, so it
 * is there well inside the pace of the polls that stand in for that stream
 * (`LIVE_POLL_FALLBACK_SECONDS`).
 */
const ARRIVES_MS = 5_000;
/** Until an open page follows a live stream that starts or ends: an event, or the poll that stands in for one. */
const FOLLOWS_MS = 20_000;
/** Until the media box takes a live stream again, right after the one before it ended. */
const TAKEN_AGAIN_MS = 20_000;
/**
 * How many messages one person sends in a row before the walk gives up on being told to
 * wait. The stack's own figure (`CHAT_MESSAGE_LIMIT_PER_USER`, five in ten seconds in
 * `.example.env`) is no field of `GET /config`, so the walk writes until one is refused.
 */
const MOST_IN_A_ROW = 12;
/** Until a wait the API named is over: the window of that limit, and a little. */
const WAIT_IS_OVER_MS = 30_000;

/**
 * Market detail pins its two outcome buttons below 768 px once their place is out of view,
 * and the bar takes its place at the end of the page. The layout check scrolls the page to
 * its end, the bar is mounted by that scroll and the page grows under it, so the check
 * would measure the bar a frame before it stands where it belongs. It is left out.
 */
const ON_MARKET_DETAIL: LayoutOptions = { ignore: ['[data-slot="pinned"]'] };

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

/**
 * The admin starts a live stream of the market: the id of the stream that is on. Right
 * after another stream ended the media box starts again and the API asks to try again
 * shortly, so the request is sent until it is taken.
 */
async function startLiveStream(admin: BrowserContext, marketId: string): Promise<string> {
  const path = `/api/admin/markets/${marketId}/broadcast`;
  let started: MarketBroadcast | undefined;
  await expect(async () => {
    const answer = await admin.request.post(`${BASE_URL}${path}`, { headers: FROM_THE_APP });
    expect(answer.ok(), `POST ${path} answered ${answer.status()}: ${await answer.text()}`).toBe(
      true,
    );
    started = (await answer.json()) as MarketBroadcast;
  }).toPass({ timeout: TAKEN_AGAIN_MS });
  const broadcast = started?.broadcast;
  if (broadcast?.status !== "LIVE") {
    throw new Error(`POST ${path} answered a live stream that is ${broadcast?.status}.`);
  }
  return broadcast.id;
}

/**
 * Takes back what the walk leaves of its market: a live stream that is still on is ended,
 * and the market is closed and voided. What goes wrong here is reported beside what the
 * walk found and never in its place.
 */
async function takeBack(admin: BrowserContext, marketId: string): Promise<void> {
  try {
    const { broadcast } = await read<MarketBroadcast>(admin, `/api/markets/${marketId}/broadcast`);
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

/**
 * The panel "Live chat" of a page of Market detail. It is found by its slot: an open
 * dialog takes the page behind it out of the reach of a role.
 */
const chatOf = (page: Page): Locator => page.locator('[data-slot="chat-panel"]');

/** The line of the chat on `page` that holds `text`. */
const lineOf = (page: Page, text: string): Locator =>
  chatOf(page).locator('[data-slot="chat-line"]').filter({ hasText: text });

/** Who a line says wrote it: the username it begins with. */
const authorOf = (line: Locator): Locator => line.locator("span").first();

/** The person of `page` writes one message, and has it in their own log. */
async function say(page: Page, text: string): Promise<void> {
  const chat = chatOf(page);
  await chat.getByRole("textbox", { name: "Message" }).fill(text);
  await chat.getByRole("button", { name: "Send", exact: true }).click();
  await expect(lineOf(page, text)).toBeVisible();
  // The field is empty for the next message.
  await expect(chat.getByRole("textbox", { name: "Message" })).toHaveValue("");
}

test("two people write in the live chat, each line carries its role, a removed message leaves every screen, and the chat goes with the stream", async ({
  people,
}, testInfo) => {
  test.setTimeout(WALK_MS);
  const looks = looksOf(testInfo);
  const admin = await asAdmin(people);
  const proposer = await people.join();
  const first = await people.join();
  const second = await people.join();
  const market = await openMarket(
    proposer.context,
    admin,
    `Does the live chat of the browser suite hold? (${RUN} ${testInfo.project.name})`,
  );

  try {
    const stream = await startLiveStream(admin, market.id);

    // Three pages of the market: the two who write, and the admin.
    const [one, two, desk] = await Promise.all(
      [first.context, second.context, admin].map(async (context) => {
        const page = await context.newPage();
        await page.goto(`/markets/${market.id}`);
        return page;
      }),
    );
    for (const page of [one, two, desk]) {
      await expect(chatOf(page)).toHaveAttribute("data-state", "open", { timeout: FOLLOWS_MS });
      await expect(chatOf(page).getByRole("log")).toContainText("No messages yet.");
    }
    await expect(one.getByRole("region", { name: "Live chat" })).toBeVisible();

    // Each of the two sees what the other writes, with the name of who wrote it.
    const hello = "Is anybody else watching this?";
    const answer = "I am, and it looks close.";
    await say(one, hello);
    await expect(lineOf(two, hello)).toBeVisible({ timeout: ARRIVES_MS });
    await expect(authorOf(lineOf(two, hello))).toHaveText(first.username);
    await say(two, answer);
    await expect(lineOf(one, answer)).toBeVisible({ timeout: ARRIVES_MS });
    await expect(authorOf(lineOf(one, answer))).toHaveText(second.username);

    // Each line carries the role supplied by the server.
    const word = "A word from the desk: keep it friendly.";
    await say(desk, word);
    for (const page of [one, two, desk]) {
      await expect(lineOf(page, word)).toBeVisible({ timeout: ARRIVES_MS });
      await expect(lineOf(page, word).locator('[data-slot="chat-role-badge"]')).toHaveText("Admin");
      await expect(lineOf(page, hello).locator('[data-slot="chat-role-badge"]')).toHaveText("User");
      await expect(lineOf(page, answer).locator('[data-slot="chat-role-badge"]')).toHaveText(
        "User",
      );
    }
    await looks.at(one, "market-chat", ON_MARKET_DETAIL);

    // Removing is the admin's: nobody else has the button.
    const removeButtons = /^Remove the message from /;
    await expect(chatOf(one).getByRole("button", { name: removeButtons })).toHaveCount(0);
    await expect(chatOf(two).getByRole("button", { name: removeButtons })).toHaveCount(0);
    await expect(chatOf(desk).getByRole("button", { name: removeButtons })).toHaveCount(3);
    await looks.at(desk, "market-chat-admin", ON_MARKET_DETAIL);

    // The admin removes the first message, and it leaves every screen.
    await chatOf(desk)
      .getByRole("button", { name: `Remove the message from ${first.username}`, exact: true })
      .click();
    const dialog = desk.getByRole("alertdialog", { name: "Remove this message?" });
    await expect(dialog.locator('[data-slot="chat-line"]')).toContainText(hello);
    await screenshot(desk, testInfo, "remove-message");
    await dialog.getByRole("button", { name: "Remove message", exact: true }).click();
    await expect(dialog).toBeHidden();
    for (const page of [desk, one, two]) {
      await expect(lineOf(page, hello)).toHaveCount(0, { timeout: ARRIVES_MS });
      await expect(lineOf(page, answer)).toBeVisible();
      await expect(lineOf(page, word)).toBeVisible();
    }
    const kept = await read<Chat>(first.context, `/api/markets/${market.id}/chat`);
    expect(kept).toMatchObject({ open: true, broadcast_id: stream });
    expect(kept.items.map((item) => item.text)).toEqual([answer, word]);

    // The live stream ends: the panel is gone, and the chat is closed and empty.
    await write(admin, `/api/broadcasts/${stream}/end`);
    for (const page of [one, two, desk]) {
      await expect(chatOf(page)).toHaveCount(0, { timeout: FOLLOWS_MS });
    }
    expect(await read<Chat>(first.context, `/api/markets/${market.id}/chat`)).toMatchObject({
      open: false,
      broadcast_id: null,
      items: [],
    });
    await looks.at(one, "market-chat-gone", ON_MARKET_DETAIL);

    // A live stream that starts afterwards opens an empty chat, on the page that is open.
    const next = await startLiveStream(admin, market.id);
    expect(next).not.toBe(stream);
    await expect(chatOf(one)).toHaveAttribute("data-state", "open", { timeout: FOLLOWS_MS });
    await expect(chatOf(one).getByRole("log")).toContainText("No messages yet.");
    await expect(chatOf(one).locator('[data-slot="chat-line"]')).toHaveCount(0);

    looks.expectAllHold();
  } finally {
    await takeBack(admin, market.id);
  }
});

test("whoever writes faster than the chat takes is told how long to wait, and the message is kept", async ({
  people,
}, testInfo) => {
  test.setTimeout(WALK_MS);
  const looks = looksOf(testInfo);
  const admin = await asAdmin(people);
  const proposer = await people.join();
  const writer = await people.join();
  const market = await openMarket(
    proposer.context,
    admin,
    `Does the live chat of the browser suite keep its pace? (${RUN} ${testInfo.project.name})`,
  );

  try {
    await startLiveStream(admin, market.id);
    const page = await writer.context.newPage();
    await page.goto(`/markets/${market.id}`);
    const chat = chatOf(page);
    await expect(chat).toHaveAttribute("data-state", "open", { timeout: FOLLOWS_MS });
    const field = chat.getByRole("textbox", { name: "Message" });
    const send = chat.getByRole("button", { name: "Send", exact: true });
    const countdown = chat.locator('[data-slot="retry-countdown"]');

    // One message after the other, until the API refuses one.
    let refused: string | null = null;
    for (let n = 1; n <= MOST_IN_A_ROW && refused === null; n += 1) {
      const text = `Message ${n} of ${MOST_IN_A_ROW} in a row`;
      await field.fill(text);
      await send.click();
      await expect(lineOf(page, text).or(countdown)).toBeVisible();
      if (await countdown.isVisible()) refused = text;
    }
    if (refused === null) {
      throw new Error(`${MOST_IN_A_ROW} messages in a row were taken, and none was refused.`);
    }

    // The API's sentence and the countdown stand in the composer, nothing can be sent
    // meanwhile, and what was written is neither lost nor in the log.
    await expect(countdown).toHaveText(/^Try again in \d+ s$/);
    await expect(chat.locator('[data-slot="inline-error"]')).toBeVisible();
    await expect(send).toBeDisabled();
    await expect(field).toHaveValue(refused);
    await expect(lineOf(page, refused)).toHaveCount(0);
    await looks.at(page, "market-chat-wait", ON_MARKET_DETAIL);

    // Once the wait is over, the same message is sent with one tap.
    await expect(countdown).toHaveCount(0, { timeout: WAIT_IS_OVER_MS });
    await expect(send).toBeEnabled();
    await send.click();
    await expect(lineOf(page, refused)).toBeVisible();
    await expect(field).toHaveValue("");

    looks.expectAllHold();
  } finally {
    await takeBack(admin, market.id);
  }
});
