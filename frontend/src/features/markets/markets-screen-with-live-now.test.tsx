import { act, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { keys } from "@/lib/api/keys";
import { installFakeEventSource } from "@/test/fake-event-source";
import {
  broadcastChangedEvent,
  config,
  liveBroadcast,
  liveBroadcasts,
  MARKET_ID,
  marketSummary,
  me,
  OTHER_MARKET_ID,
  readyEvent,
  TODAY,
} from "@/test/fixtures";
import { apiError, deferred, type Handler, json, mockApi, noAnswer, type Route } from "@/test/http";
import { navigation } from "@/test/navigation";
import { renderWithProviders } from "@/test/render";

import { MarketsScreen } from "./index";

/**
 * "Live now" (`LiveNowRow` of `features/broadcast`) inside the Markets screen: neither is
 * a stand-in. The other tests of the screen put nothing in the place of the row, and the
 * row's own tests show it alone; this is the check of where it stands on the screen and
 * that the two fit together.
 */

const MARKETS: Route = "GET /api/markets";
const LIVE: Route = "GET /api/broadcasts/live";
/** The window getting the focus back also reads the balance of the top bar again. */
const ME: Route = "GET /api/me";

/** The market whose live stream is on: `liveBroadcast()` is the live stream of `marketSummary()`. */
const POLYBAHN = liveBroadcast().question;
const MENSA = "Will the Mensa serve Älplermagronen before the end of October?";

const polybahn = marketSummary();
const mensa = marketSummary({
  id: OTHER_MARKET_ID,
  question: MENSA,
  p_yes: 0.31,
  p_no: 0.69,
  volume_cr: "80",
});

const BOTH = json(200, { items: [polybahn, mensa] });
/** No live stream is on. */
const NOTHING = liveBroadcasts({ items: [] });

/** The screen with the live streams `live` answers and the markets `markets` answers. */
function show(
  live: Handler,
  markets: Handler = BOTH,
  options: Parameters<typeof renderWithProviders>[1] = {},
) {
  const api = mockApi({ [MARKETS]: markets, [LIVE]: live, [ME]: json(200, me()) });
  const rendered = renderWithProviders(<MarketsScreen />, options);
  return { api, ...rendered };
}

/** The content column of the screen: what stands under the top bar. */
const column = () => screen.getByRole("main");
const title = () => screen.getByRole("heading", { level: 1 });
const row = () => document.querySelector<HTMLElement>("[data-slot=live-now]");
const rowLink = () => screen.findByRole("link", { name: `Live now: ${POLYBAHN}` });
const chips = () => screen.getByRole("group", { name: "Status" });
const chip = (name: string) => within(chips()).getByRole("button", { name });
const cards = () => document.querySelector<HTMLElement>("[data-slot=market-list]")!;
const questions = () =>
  screen.getAllByRole("article").map((card) => within(card).getByRole("heading").textContent);
const follows = (earlier: Element, later: Element) =>
  (earlier.compareDocumentPosition(later) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0;

/**
 * The row is a part of the content column itself, the first under the top bar and
 * directly above the status chips: nothing wraps it, so the 24 px between the parts of
 * the screen are its distance to the chips.
 */
function expectRowAboveTheChips() {
  expect(row()).not.toBeNull();
  expect(row()!.parentElement).toBe(column());
  expect(row()!.previousElementSibling).toBe(title());
  expect(row()!.nextElementSibling).toBe(chips());
}

/** Nothing stands, and nothing holds a place, between the top bar and the status chips. */
function expectNoRow() {
  expect(row()).toBeNull();
  expect(chips().parentElement).toBe(column());
  expect(chips().previousElementSibling).toBe(title());
}

// The list sets a timer for the earliest close time it shows: the tests stand on one day.
beforeEach(() => {
  vi.useFakeTimers({ now: TODAY, toFake: ["Date"] });
});

afterEach(() => {
  vi.useRealTimers();
});

describe("Markets, with the live streams that are on", () => {
  test("a live stream that is on stands in one row above the status chips, and the row leads to its market", async () => {
    const { api, user } = show(json(200, liveBroadcasts()));

    const link = await rowLink();
    await screen.findByRole("heading", { name: MENSA });

    // Top to bottom: "Live now", the status chips, the cards.
    expectRowAboveTheChips();
    expect(follows(chips(), cards())).toBe(true);
    // One row of 44 px, the question on one line.
    expect(within(row()!).getAllByRole("link")).toEqual([link]);
    expect(link).toHaveClass("h-11");
    expect(within(link).getByText(POLYBAHN)).toHaveClass("truncate");

    // The cards under it are as they are without it, the card of that market among them.
    expect(questions()).toEqual([POLYBAHN, MENSA]);
    expect(screen.getAllByRole("link", { name: POLYBAHN })).toHaveLength(1);
    expect(screen.getAllByRole("heading", { name: POLYBAHN })).toHaveLength(1);
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(chip("Open")).toHaveAttribute("aria-pressed", "true");

    // One read of the live streams, which names no status: it is not the list of a chip.
    expect(api.calls(LIVE)).toHaveLength(1);
    expect(api.lastCall(LIVE)?.path).toBe("/api/broadcasts/live");
    expect([...api.lastCall(LIVE)!.query.keys()]).toEqual([]);
    expect(api.calls(MARKETS)).toHaveLength(1);

    expect(link).toHaveAttribute("href", `/markets/${MARKET_ID}`);
    await user.click(link);
    expect(navigation.href).toBe(`/markets/${MARKET_ID}`);
  });

  test("with no live stream nothing stands between the top bar and the status chips", async () => {
    const { api, client } = show(json(200, NOTHING));

    await screen.findByRole("heading", { name: MENSA });
    await waitFor(() => expect(client.getQueryData(keys.broadcasts.live)).toEqual(NOTHING));

    expectNoRow();
    // The cards are the one list of the screen, and nothing is said in the row's place.
    expect(screen.getAllByRole("list")).toEqual([cards()]);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(questions()).toEqual([POLYBAHN, MENSA]);
    expect(api.calls(LIVE)).toHaveLength(1);
  });

  test("the screen does not wait for the live streams: nothing holds the row's place while they load", async () => {
    const answer = deferred();
    const { api } = show(answer.handler);

    await screen.findByRole("heading", { name: MENSA });
    expect(api.calls(LIVE)).toHaveLength(1);
    expectNoRow();
    // No skeleton stands in for the row.
    expect(screen.queryByRole("status")).not.toBeInTheDocument();

    await act(async () => answer.resolve(json(200, liveBroadcasts())));

    await rowLink();
    expectRowAboveTheChips();
    expect(questions()).toEqual([POLYBAHN, MENSA]);
    expect(api.calls(MARKETS)).toHaveLength(1);
  });

  test.each([
    ["an error of the server", () => apiError(500, "INTERNAL_ERROR", "Something went wrong.")],
    ["no answer", noAnswer],
  ])(
    "after %s for the live streams the screen says nothing about it and keeps its cards",
    async (_what, answer) => {
      const { api, client } = show(answer);

      await screen.findByRole("heading", { name: MENSA });
      await waitFor(() => expect(client.getQueryState(keys.broadcasts.live)?.status).toBe("error"));

      expectNoRow();
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      expect(questions()).toEqual([POLYBAHN, MENSA]);
      expect(api.calls(MARKETS)).toHaveLength(1);
    },
  );

  test("the row does not wait for the markets, and stays when that read fails", async () => {
    const answer = deferred();
    const { api, user } = show(json(200, liveBroadcasts()), answer.handler);

    await rowLink();
    expect(screen.getByRole("status")).toHaveTextContent("Loading markets");
    expectRowAboveTheChips();

    await act(async () =>
      answer.resolve(apiError(500, "INTERNAL_ERROR", "Something went wrong on our side.")),
    );

    // The banner is the list's: the row above the chips stays as it is.
    const banner = await screen.findByRole("alert");
    expect(banner).toHaveTextContent("Something went wrong on our side.");
    expect(follows(chips(), banner)).toBe(true);
    expectRowAboveTheChips();

    api.on(MARKETS, BOTH);
    await user.click(within(banner).getByRole("button", { name: "Try again" }));

    await screen.findByRole("heading", { name: MENSA });
    expectRowAboveTheChips();
    // "Try again" of the list reads the markets, and the live streams are left alone.
    expect(api.calls(MARKETS)).toHaveLength(2);
    expect(api.calls(LIVE)).toHaveLength(1);
  });

  test("a status chip reads the markets again and not the live streams, and the row stays above an empty list", async () => {
    const { api, user } = show(json(200, liveBroadcasts()));
    await rowLink();
    await screen.findByRole("heading", { name: MENSA });
    api.on(MARKETS, json(200, { items: [] }));

    await user.click(chip("Closed"));

    expect(await screen.findByText(/No markets yet/)).toBeInTheDocument();
    expect(chip("Closed")).toHaveAttribute("aria-pressed", "true");
    expect(api.calls(MARKETS)).toHaveLength(2);
    expect(api.lastCall(MARKETS)?.query.getAll("status")).toEqual(["CLOSED"]);
    // The live stream is on whatever the chip lists.
    expect(await rowLink()).toHaveAttribute("href", `/markets/${MARKET_ID}`);
    expectRowAboveTheChips();
    expect(api.calls(LIVE)).toHaveLength(1);
  });

  test("the window getting the focus back reads the live streams again with the markets", async () => {
    const { api, client } = show(json(200, NOTHING));
    await screen.findByRole("heading", { name: MENSA });
    await waitFor(() => expect(client.getQueryData(keys.broadcasts.live)).toEqual(NOTHING));
    api.on(LIVE, json(200, liveBroadcasts()));

    act(() => {
      window.dispatchEvent(new Event("visibilitychange"));
    });

    await rowLink();
    expectRowAboveTheChips();
    expect(api.calls(LIVE)).toHaveLength(2);
    expect(api.calls(MARKETS)).toHaveLength(2);
  });

  describe("following the event stream", () => {
    const live = {
      live: true,
      // An announcement is read at once.
      config: config({ live_announce_spread_ms: 0 }),
    };

    test("a live stream that starts while the screen is open appears above the chips without a reload, and one that ends leaves", async () => {
      const streams = installFakeEventSource();
      const { api, client } = show(json(200, NOTHING), BOTH, live);
      await screen.findByRole("heading", { name: MENSA });
      await waitFor(() => expect(api.calls(LIVE)).toHaveLength(1));

      // The app's one stream, and the one topic on it is the row's.
      expect(streams.instances).toHaveLength(1);
      expect(streams.latest().topics).toEqual(["markets"]);
      act(() => streams.latest().open());
      // The stream is open: the live streams are read once more, and the markets are not.
      act(() => streams.latest().send(readyEvent()));
      await waitFor(() => expect(api.calls(LIVE)).toHaveLength(2));
      // That read has answered: one that is on its way would answer an announcement too.
      await waitFor(() => expect(client.isFetching({ queryKey: keys.broadcasts.live })).toBe(0));
      expectNoRow();

      // Somebody goes live.
      api.on(LIVE, json(200, liveBroadcasts()));
      act(() => streams.latest().send(broadcastChangedEvent()));

      await rowLink();
      expectRowAboveTheChips();
      expect(api.calls(LIVE)).toHaveLength(3);

      // The live stream ends.
      api.on(LIVE, json(200, NOTHING));
      act(() => streams.latest().send(broadcastChangedEvent()));

      await waitFor(() => expect(row()).toBeNull());
      expectNoRow();
      expect(api.calls(LIVE)).toHaveLength(4);
      // An announcement is about live streams: the list of markets is not read again,
      // and the cards stand as they stood.
      expect(api.calls(MARKETS)).toHaveLength(1);
      expect(questions()).toEqual([POLYBAHN, MENSA]);
      expect(streams.instances).toHaveLength(1);
    });

    test("the screen opens no stream of its own: outside the app's provider none is opened", async () => {
      const streams = installFakeEventSource();
      show(json(200, liveBroadcasts()));

      await rowLink();
      await screen.findByRole("heading", { name: MENSA });

      expect(streams.instances).toHaveLength(0);
    });
  });
});
