import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { marketBroadcastQuery } from "@/lib/api/queries";
import type { MarketBroadcastResponse, MarketDetailResponse, MarketStatus } from "@/lib/api/schema";
import { installFakeEventSource } from "@/test/fake-event-source";
import {
  adminMarketDetail,
  broadcastChangedEvent,
  broadcastIn,
  broadcastPerson,
  broadcastViewer,
  config,
  MARKET_ID,
  marketBroadcast,
  me,
  noBroadcast,
  readyEvent,
} from "@/test/fixtures";
import { apiError, deferred, type Handler, json, mockApi, type Route } from "@/test/http";
import { renderWithProviders } from "@/test/render";

import { AdminMarketScreen } from "./admin-market-screen";

/**
 * The block "Live stream" (`BroadcastPanel`) inside the admin view of a market: neither
 * is a stand-in. The other tests of the screen put nothing in the place of the block, and
 * the block's own tests show it alone; this is the check of where it stands on the screen
 * and that the two fit together.
 */

const MARKET: Route = "GET /api/markets/{market_id}";
const BROADCAST: Route = "GET /api/markets/{market_id}/broadcast";
const ME: Route = "GET /api/me";
const APPROVE: Route = "POST /api/admin/markets/{market_id}/approve";

const QUESTION = "Will the Polybahn run without interruption during the exam session?";
const NOT_FOUND = apiError(404, "NOT_FOUND", "Not found.");

const adminUser = me({ role: "ADMIN" });

/** No live stream, and an admin who may start one. */
const nothing = () => noBroadcast({ can_start_as_admin: true });
/** The proposer of the market asked for a live stream, and an admin decides. */
const requested = () =>
  marketBroadcast({
    broadcast: broadcastIn("REQUESTED", { requested_by: broadcastPerson() }),
    viewer: broadcastViewer({ can_watch: false, can_approve: true, can_refuse: true }),
  });

const proposal = () =>
  adminMarketDetail({
    status: "PROPOSED",
    trading_open: false,
    tier: null,
    b_cr: "0",
    max_loss_cr: "0",
    opened_at: null,
  });

function show(
  market: MarketDetailResponse,
  broadcast: MarketBroadcastResponse | Handler,
  routes: Partial<Record<Route, Handler>> = {},
  options: Parameters<typeof renderWithProviders>[1] = {},
) {
  const read = broadcast instanceof Response || typeof broadcast === "function";
  const api = mockApi({
    [MARKET]: json(200, market),
    [BROADCAST]: read ? broadcast : json(200, broadcast),
    [ME]: json(200, adminUser),
    ...routes,
  });
  const rendered = renderWithProviders(<AdminMarketScreen marketId={MARKET_ID} />, {
    me: adminUser,
    ...options,
  });
  return { api, ...rendered };
}

const block = () => screen.findByRole("region", { name: "Live stream" });
const absent = () =>
  expect(document.querySelector("[data-slot=broadcast-panel]")).not.toBeInTheDocument();
/** The sentence of the block, once its read has answered. */
const says = (region: HTMLElement, sentence: string) =>
  waitFor(() => expect(region.querySelector("p[role=status]")).toHaveTextContent(sentence));
const details = () => screen.getByRole("heading", { level: 2, name: "Details" });
const bar = () => screen.getByRole("region", { name: "Actions" });
const actions = () =>
  within(bar())
    .getAllByRole("button")
    .map((button) => button.textContent);
const follows = (earlier: Element, later: Element) =>
  (earlier.compareDocumentPosition(later) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0;

describe("Admin: one market, with the block of its live stream", () => {
  test("the block stands under the rules and the facts and above the action bar, and reads the live stream of the screen's market", async () => {
    const { api } = show(adminMarketDetail(), nothing());

    const region = await block();
    await says(region, "No live stream.");
    expect(within(region).getByRole("link", { name: "Start a live stream" })).toHaveAttribute(
      "href",
      `/markets/${MARKET_ID}/live`,
    );

    expect(
      screen.getAllByRole("heading", { level: 2 }).map((heading) => heading.textContent),
    ).toEqual(["Rules", "Details", "Live stream"]);
    expect(follows(details(), region)).toBe(true);
    expect(follows(region, bar())).toBe(true);
    // A section of the screen as the action bar is one: nothing wraps it, and the 24 px
    // between the sections of the screen are its distance to both.
    expect(bar().previousElementSibling).toBe(region);
    expect(region.previousElementSibling).toContainElement(details());

    expect(api.calls(BROADCAST)).toHaveLength(1);
    expect(api.lastCall(BROADCAST)?.path).toBe(`/api/markets/${MARKET_ID}/broadcast`);
    // The market is the screen's to read, and it reads it once.
    expect(api.calls(MARKET)).toHaveLength(1);
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  });

  test("a request for a live stream is decided in the block, and the action bar keeps the actions of the market", async () => {
    show(adminMarketDetail(), requested());

    const region = await block();
    await says(region, "Requested by cab_coffee (bea.beispiel@ethz.ch) on 11 Oct 2026, 10:30.");
    expect(
      within(region)
        .getAllByRole("button")
        .map((button) => button.textContent),
    ).toEqual(["Approve", "Refuse"]);
    expect(actions()).toEqual(["Halt", "Close"]);
    // Two lines of the screen are announced when they change: the status of the market
    // under the question, and the sentence of the block.
    expect(screen.getAllByRole("status").map((status) => status.textContent)).toEqual([
      "Open",
      "Requested by cab_coffee (bea.beispiel@ethz.ch) on 11 Oct 2026, 10:30.",
    ]);
  });

  test("a proposal has no block, not for a moment: its read answers NOT_FOUND, and nothing stands between the facts and the action bar", async () => {
    const answer = deferred();
    const { api, client } = show(proposal(), answer.handler);

    await screen.findByRole("heading", { level: 1, name: QUESTION });
    // The read is made all the same: the API says whether a market has a live stream.
    await waitFor(() => expect(api.calls(BROADCAST)).toHaveLength(1));
    absent();
    expect(bar().previousElementSibling).toContainElement(details());

    answer.resolve(NOT_FOUND);
    await waitFor(() =>
      expect(client.getQueryState(marketBroadcastQuery(MARKET_ID).queryKey)?.status).toBe("error"),
    );

    absent();
    expect(screen.queryByRole("heading", { name: "Live stream" })).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(bar().previousElementSibling).toContainElement(details());
    expect(actions()).toEqual(["Approve", "Reject"]);
    expect(api.calls(BROADCAST)).toHaveLength(1);
  });

  test("after Approve on a proposal the block is there without a reload: the screen updates from the answer, and the block reads its live stream again", async () => {
    const opened = adminMarketDetail({ status: "OPEN", tier: "MEDIUM", b_cr: "2000" });
    const { api, client, user } = show(proposal(), NOT_FOUND, { [APPROVE]: json(200, opened) });
    await screen.findByRole("heading", { level: 1, name: QUESTION });
    await waitFor(() =>
      expect(client.getQueryState(marketBroadcastQuery(MARKET_ID).queryKey)?.status).toBe("error"),
    );
    absent();

    await user.click(within(bar()).getByRole("button", { name: "Approve" }));
    const dialog = await screen.findByRole("dialog", { name: "Approve market" });
    await user.click(within(dialog).getByRole("radio", { name: "Medium" }));
    // A market that is open has a live stream to ask for.
    api.on(BROADCAST, json(200, nothing()));
    await user.click(within(dialog).getByRole("button", { name: "Approve" }));

    const region = await block();
    await says(region, "No live stream.");
    expect(within(region).getByRole("link", { name: "Start a live stream" })).toBeInTheDocument();
    expect(actions()).toEqual(["Halt", "Close"]);
    expect(bar().previousElementSibling).toBe(region);
    expect(api.calls(BROADCAST)).toHaveLength(2);
    // The answer of the action is the market: it is not asked for a second time.
    expect(api.calls(MARKET)).toHaveLength(1);
  });

  test("the market does not wait for its live stream: the block holds its place while it reads, and a read that fails leaves the market and its actions standing", async () => {
    const answer = deferred();
    const { api, user } = show(adminMarketDetail(), answer.handler);

    await screen.findByRole("heading", { level: 1, name: QUESTION });
    const region = await block();
    expect(within(region).getByRole("status")).toHaveTextContent("Loading");
    expect(screen.getByRole("heading", { level: 2, name: "Rules" })).toBeInTheDocument();
    expect(actions()).toEqual(["Halt", "Close"]);
    expect(bar().previousElementSibling).toBe(region);

    answer.resolve(apiError(500, "INTERNAL_ERROR", "Something went wrong on our side."));

    // The banner is the block's: the screen under and above it stays as it is.
    expect(await within(region).findByRole("alert")).toHaveTextContent(
      "Something went wrong on our side.",
    );
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1, name: QUESTION })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "Details" })).toBeInTheDocument();
    expect(actions()).toEqual(["Halt", "Close"]);

    api.on(BROADCAST, json(200, nothing()));
    await user.click(within(region).getByRole("button", { name: "Try again" }));

    await says(region, "No live stream.");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    // "Try again" of the block reads the live stream, and the market is left alone.
    expect(api.calls(BROADCAST)).toHaveLength(2);
    expect(api.calls(MARKET)).toHaveLength(1);
  });

  test("a request that arrives while the screen is open shows in the block without a reload, and the screen itself names nothing to the event stream", async () => {
    const streams = installFakeEventSource();
    const { api } = show(
      adminMarketDetail(),
      nothing(),
      {},
      {
        live: true,
        // An announcement is read at once.
        config: config({ live_announce_spread_ms: 0 }),
      },
    );
    const region = await block();
    await says(region, "No live stream.");
    // The block is there once the market is read, and the stream is exchanged for one that
    // names its topic. It is the one topic: the market itself follows nothing.
    await waitFor(() => expect(streams.latest().topics).toEqual([`broadcast:${MARKET_ID}`]));
    // The stream is open: the live stream is read once more, and the market is not.
    act(() => streams.latest().send(readyEvent()));
    await waitFor(() => expect(api.calls(BROADCAST)).toHaveLength(2));

    // The proposer asks for a live stream.
    api.on(BROADCAST, json(200, requested()));
    act(() => streams.latest().send(broadcastChangedEvent()));

    await says(region, "Requested by cab_coffee (bea.beispiel@ethz.ch) on 11 Oct 2026, 10:30.");
    expect(within(region).getByRole("button", { name: "Approve" })).toBeInTheDocument();
    expect(actions()).toEqual(["Halt", "Close"]);
    expect(bar().previousElementSibling).toBe(region);
    expect(api.calls(BROADCAST)).toHaveLength(3);
    // The announcement is about the live stream: the market is not read again.
    expect(api.calls(MARKET)).toHaveLength(1);
  });

  test.each([
    "PROPOSED",
    "EXPIRED",
    "REJECTED",
    "OPEN",
    "HALTED",
    "CLOSED",
    "RESOLVED",
    "VOID",
  ] as const satisfies readonly MarketStatus[])(
    "the screen holds no status of a market against the block: for one that is %s the read is made, and its answer is shown",
    async (status) => {
      // The API answers NOT_FOUND for a proposal. Here it answers for every status alike,
      // so that what is shown can only come from the answer.
      const { api } = show(
        adminMarketDetail({ status, trading_open: status === "OPEN" }),
        nothing(),
      );

      const region = await block();
      await says(region, "No live stream.");
      expect(within(region).getByRole("link", { name: "Start a live stream" })).toBeInTheDocument();
      expect(follows(details(), region)).toBe(true);
      expect(api.calls(BROADCAST)).toHaveLength(1);
    },
  );
});
