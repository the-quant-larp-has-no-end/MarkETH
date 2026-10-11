import { act, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import type { AdminBroadcastListResponse, MarketSummary } from "@/lib/api/schema";
import { installFakeEventSource } from "@/test/fake-event-source";
import {
  adminBroadcasts,
  broadcastChangedEvent,
  config,
  MARKET_ID,
  marketSummary,
  me,
  OTHER_MARKET_ID,
  readyEvent,
  TODAY,
} from "@/test/fixtures";
import {
  apiError,
  deferred,
  type Handler,
  json,
  mockApi,
  type MockRequest,
  type Route,
} from "@/test/http";
import { navigation } from "@/test/navigation";
import { renderWithProviders } from "@/test/render";

import { AdminMarketsScreen } from "./admin-markets-screen";

/**
 * The table "Live streams" (`BroadcastQueue`) inside the admin's Markets screen: neither
 * is a stand-in. The other tests of the screen put nothing in the place of the table, and
 * the table's own tests show it alone; this is the check of where it stands on the screen
 * and that the two fit together.
 */

const MARKETS: Route = "GET /api/markets";
const QUEUE: Route = "GET /api/admin/broadcasts";

const QUESTION = "Will the Polybahn run without interruption during the exam session?";
const NOTHING: AdminBroadcastListResponse = { items: [] };

const admin = { me: me({ role: "ADMIN" }) };

const proposal = marketSummary({
  id: OTHER_MARKET_ID,
  question: "Will the ASVZ open a second climbing wall before the end of the semester?",
  status: "PROPOSED",
  trading_open: false,
  p_yes: 0.5,
  p_no: 0.5,
  volume_cr: "0",
  close_at: "2026-12-11T17:00:00Z",
});
const open = marketSummary();

/** The screen with the live streams `queue` answers, the proposals `queued` and the markets `rest`. */
function show(
  queue: AdminBroadcastListResponse | Handler,
  queued: MarketSummary[] = [],
  rest: MarketSummary[] = [],
  options: Parameters<typeof renderWithProviders>[1] = {},
) {
  const read = queue instanceof Response || typeof queue === "function";
  const api = mockApi({
    [MARKETS]: (request: MockRequest) =>
      json(200, {
        items: request.query.getAll("status").join() === "PROPOSED" ? queued : rest,
      }),
    [QUEUE]: read ? queue : json(200, queue),
  });
  const rendered = renderWithProviders(<AdminMarketsScreen />, { ...admin, ...options });
  return { api, ...rendered };
}

const section = (name: string) => screen.getByRole("region", { name });
const absent = () =>
  expect(document.querySelector("[data-slot=broadcast-queue]")).not.toBeInTheDocument();
const headings = () =>
  screen.getAllByRole("heading", { level: 2 }).map((heading) => heading.textContent);
const tables = () => screen.getAllByRole("table").map((table) => table.getAttribute("aria-label"));
/** The last thing of the tabs, at every width. */
const scanLink = () => screen.getByRole("link", { name: "Scan vouchers" });
const follows = (earlier: Element, later: Element) =>
  (earlier.compareDocumentPosition(later) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0;

// The screen sets a timer for the earliest close time among its proposals in review: the
// tests stand on one day.
beforeEach(() => {
  vi.useFakeTimers({ now: TODAY, toFake: ["Date"] });
});

afterEach(() => {
  vi.useRealTimers();
});

describe("Admin: markets, with the table of the live streams", () => {
  test("the table stands between the tabs and the proposals, under a heading of its own, and a row opens the admin view of its market", async () => {
    navigation.go("/admin");
    const { api, user } = show(adminBroadcasts(), [proposal], [open]);

    const queue = await screen.findByRole("table", { name: "Live streams" });
    await within(await screen.findByRole("table", { name: "Markets" })).findByText(open.question);
    await screen.findByRole("table", { name: "Proposals" });

    expect(headings()).toEqual(["Live streams", "Proposals", "Markets"]);
    expect(tables()).toEqual(["Live streams", "Proposals", "Markets"]);
    const streams = section("Live streams");
    expect(follows(scanLink(), streams)).toBe(true);
    expect(follows(streams, section("Proposals"))).toBe(true);
    // A section of the screen as "Proposals" is one: nothing wraps it, and the 24 px
    // between the sections of the screen are its distance to both.
    expect(streams.nextElementSibling).toBe(section("Proposals"));
    expect(streams.previousElementSibling).toContainElement(scanLink());
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);

    expect(api.calls(QUEUE)).toHaveLength(1);
    expect(api.lastCall(QUEUE)?.path).toBe("/api/admin/broadcasts");
    // The two lists of the screen are asked for as they are without the table.
    expect(api.calls(MARKETS)).toHaveLength(2);

    const link = within(queue).getByRole("link", { name: QUESTION });
    expect(link).toHaveAttribute("href", `/admin/markets/${MARKET_ID}`);
    // A tap anywhere on the row does what its link does.
    await user.click(within(queue).getByText("cab_coffee"));
    expect(navigation.pathname).toBe(`/admin/markets/${MARKET_ID}`);
  });

  test("with no live stream that waits or is on, nothing stands between the tabs and the proposals", async () => {
    const { api } = show(NOTHING);

    expect(await screen.findByText("No proposals waiting.")).toBeInTheDocument();
    expect(await screen.findByText("No markets.")).toBeInTheDocument();
    await waitFor(() => absent());

    expect(headings()).toEqual(["Proposals", "Markets"]);
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(section("Proposals").previousElementSibling).toContainElement(scanLink());
    expect(api.calls(QUEUE)).toHaveLength(1);
  });

  test("the lists of the screen do not wait for the live streams, and stay when that read fails", async () => {
    const answer = deferred();
    const { api, user } = show(answer.handler, [proposal], [open]);

    await within(await screen.findByRole("table", { name: "Markets" })).findByText(open.question);
    await within(await screen.findByRole("table", { name: "Proposals" })).findByText(
      proposal.question,
    );
    const streams = section("Live streams");
    expect(within(streams).getByRole("status")).toHaveTextContent("Loading");
    expect(follows(streams, section("Proposals"))).toBe(true);

    answer.resolve(
      apiError(503, "SERVICE_UNAVAILABLE", "This is temporarily unavailable. Try again shortly."),
    );

    // The banner is the table's: the two lists under it stay as they are.
    expect(await within(streams).findByRole("alert")).toHaveTextContent(
      "This is temporarily unavailable. Try again shortly.",
    );
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(tables()).toEqual(["Proposals", "Markets"]);

    api.on(QUEUE, json(200, adminBroadcasts()));
    await user.click(within(streams).getByRole("button", { name: "Try again" }));

    expect(await screen.findByRole("table", { name: "Live streams" })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    // "Try again" of the table reads the live streams, and the two lists are left alone.
    expect(api.calls(QUEUE)).toHaveLength(2);
    expect(api.calls(MARKETS)).toHaveLength(2);
  });

  test("a choice of the status select reads the markets again and not the live streams", async () => {
    const { api, user } = show(adminBroadcasts(), [], [open]);
    await screen.findByRole("table", { name: "Live streams" });
    await within(await screen.findByRole("table", { name: "Markets" })).findByText(open.question);

    await user.selectOptions(screen.getByLabelText("Status"), "Open");

    await waitFor(() => expect(api.calls(MARKETS)).toHaveLength(3));
    expect(api.calls(QUEUE)).toHaveLength(1);
    expect(headings()).toEqual(["Live streams", "Proposals", "Markets"]);
  });

  test("a request that arrives while the screen is open shows above the proposals without a reload, and the screen itself names nothing to the event stream", async () => {
    const streams = installFakeEventSource();
    const { api } = show(NOTHING, [proposal], [open], {
      live: true,
      // An announcement is read at once.
      config: config({ live_announce_spread_ms: 0 }),
    });
    await within(await screen.findByRole("table", { name: "Markets" })).findByText(open.question);
    await waitFor(() => absent());
    // The one topic is the table's.
    expect(streams.latest().topics).toEqual(["markets"]);
    // The stream is open: the live streams are read once more, and the two lists are not.
    act(() => streams.latest().send(readyEvent()));
    await waitFor(() => expect(api.calls(QUEUE)).toHaveLength(2));

    // Somebody asks for a live stream.
    api.on(QUEUE, json(200, adminBroadcasts()));
    act(() => streams.latest().send(broadcastChangedEvent()));

    const queue = await screen.findByRole("table", { name: "Live streams" });
    expect(await within(queue).findByText(QUESTION)).toBeInTheDocument();
    expect(headings()).toEqual(["Live streams", "Proposals", "Markets"]);
    expect(section("Live streams").nextElementSibling).toBe(section("Proposals"));
    expect(api.calls(QUEUE)).toHaveLength(3);
    // The announcement is about live streams: the two lists of the screen are not read again.
    expect(api.calls(MARKETS)).toHaveLength(2);
  });
});
