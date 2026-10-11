import type { QueryClient } from "@tanstack/react-query";
import { act, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";

import { useShellState } from "@/components/shell/shell-store";
import { keys } from "@/lib/api/keys";
import type { MarketBroadcastResponse } from "@/lib/api/schema";
import { installFakeEventSource } from "@/test/fake-event-source";
import {
  BROADCAST_ID,
  broadcastChangedEvent,
  broadcastIn,
  broadcastViewer,
  config,
  MARKET_ID,
  marketBroadcast,
  marketDetail,
  noBroadcast,
  OTHER_MARKET_ID,
  readyEvent,
} from "@/test/fixtures";
import { apiError, deferred, json, mockApi, type Route } from "@/test/http";
import { setViewport } from "@/test/media";
import { createTestQueryClient, renderWithProviders } from "@/test/render";

import { MarketBroadcast, type MarketBroadcastProps } from "./market-broadcast";
import { FakeHls, installBrowser, resetHls, showFrame } from "./testing";

vi.mock("hls.js", async () => (await import("./testing")).hlsModule);
vi.mock("@/features/chat", async () => (await import("./testing")).chatFeature);

const BROADCAST: Route = "GET /api/markets/{market_id}/broadcast";
const REQUEST: Route = "POST /api/markets/{market_id}/broadcast/request";

const QUESTION = marketDetail().question;
const PLAYLIST = `/api/broadcasts/${BROADCAST_ID}/hls/index.m3u8`;
const STUDIO = `/markets/${MARKET_ID}/live`;

/** A live stream that is on, as a viewer with a place sees it. */
const ON_AIR = marketBroadcast();
/** LIVE, and nothing to play yet. */
const STARTING = marketBroadcast({
  broadcast: broadcastIn("LIVE", { on_air: false, playlist_url: null }),
  viewer: broadcastViewer({ can_watch: false }),
});
const FULL = marketBroadcast({ viewer: broadcastViewer({ can_watch: false, full: true }) });
/** After the end, a viewer is shown no live stream. */
const OVER = noBroadcast();
/** The proposer's side: a live stream of the viewer's own, in one status. */
const own = (
  status: Parameters<typeof broadcastIn>[0],
  viewer: Parameters<typeof broadcastViewer>[0] = {},
  overrides: Parameters<typeof broadcastIn>[1] = {},
) =>
  marketBroadcast({
    broadcast: broadcastIn(status, overrides),
    viewer: broadcastViewer({ is_broadcaster: true, can_watch: status === "LIVE", ...viewer }),
  });

/** Says what the shell does with its tab bar. */
function TabBarProbe() {
  return <span data-testid="tab-bar">{useShellState().tabBarHidden ? "hidden" : "shown"}</span>;
}

beforeEach(() => {
  resetHls();
});

function renderSection(
  answer: MarketBroadcastResponse | Response | (() => Promise<Response>),
  props: Partial<MarketBroadcastProps> = {},
  options: Parameters<typeof renderWithProviders>[1] = {},
) {
  installBrowser();
  const api = mockApi({
    [BROADCAST]:
      answer instanceof Response || typeof answer === "function" ? answer : json(200, answer),
  });
  // Market detail has read the market: its question names the video box.
  const client = createTestQueryClient();
  client.setQueryData(keys.markets.detail(MARKET_ID), marketDetail());
  const view = renderWithProviders(
    <div data-testid="column">
      <MarketBroadcast marketId={MARKET_ID} {...props} />
      <TabBarProbe />
    </div>,
    { client, ...options },
  );
  return { ...view, api };
}

const section = () => document.querySelector<HTMLElement>("[data-slot=live-stream]");
const card = () => within(section()!);
const shown = () => screen.findByRole("heading", { level: 2, name: "Live stream" });
const video = () => screen.queryByLabelText<HTMLVideoElement>(`Live stream: ${QUESTION}`);
const chat = () => screen.queryByTestId("chat-panel");
const badge = () => section()!.querySelector("[data-slot=badge]");

/** The answer changed: the read is asked again, as the stream's event does it. */
async function becomes(
  api: ReturnType<typeof mockApi>,
  client: QueryClient,
  answer: MarketBroadcastResponse,
) {
  api.on(BROADCAST, json(200, answer));
  await act(() =>
    client.invalidateQueries({ queryKey: keys.markets.broadcast(MARKET_ID), exact: true }),
  );
}

describe("the section has content or is absent", () => {
  /** Nothing of the section: no frame, no sentence, no banner. */
  const absent = () => {
    expect(section()).toBeNull();
    expect(screen.getByTestId("column").children).toHaveLength(1);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(document.querySelector("[data-slot=skeleton]")).toBeNull();
    expect(chat()).not.toBeInTheDocument();
  };

  test("while its read loads, nothing stands in its place", async () => {
    const answer = deferred();
    const { api } = renderSection(answer.handler);

    await waitFor(() => expect(api.calls(BROADCAST)).toHaveLength(1));
    absent();

    answer.resolve(json(200, ON_AIR));
    expect(await shown()).toBeInTheDocument();
  });

  test("a read that fails shows nothing and no banner", async () => {
    const { api } = renderSection(apiError(500, "INTERNAL_ERROR", "Something went wrong."));

    await waitFor(() => expect(api.calls(BROADCAST)).toHaveLength(1));
    await act(() => new Promise((resolve) => setTimeout(resolve, 20)));
    absent();
  });

  test("a market the API answers NOT_FOUND for, as for a proposal, shows nothing and no banner", async () => {
    const { api } = renderSection(apiError(404, "NOT_FOUND", "Not found."));

    await waitFor(() => expect(api.calls(BROADCAST)).toHaveLength(1));
    await act(() => new Promise((resolve) => setTimeout(resolve, 20)));
    absent();
  });

  test("NOT_FOUND takes a section away that was shown", async () => {
    const { api, client } = renderSection(ON_AIR);
    await shown();

    api.on(BROADCAST, apiError(404, "NOT_FOUND", "Not found."));
    await act(() =>
      client.invalidateQueries({ queryKey: keys.markets.broadcast(MARKET_ID), exact: true }),
    );

    await waitFor(absent);
  });

  test("a market without a live stream, for a viewer with nothing to do", async () => {
    const { api } = renderSection(noBroadcast());

    await waitFor(() => expect(api.calls(BROADCAST)).toHaveLength(1));
    await act(() => new Promise((resolve) => setTimeout(resolve, 20)));
    absent();
  });

  test("a live stream that ended before this screen was open, and is not the viewer's own", async () => {
    // An admin is shown the latest live stream of a market in every status.
    const { api } = renderSection(
      marketBroadcast({
        broadcast: broadcastIn("ENDED"),
        viewer: broadcastViewer({ can_watch: false }),
      }),
    );

    await waitFor(() => expect(api.calls(BROADCAST)).toHaveLength(1));
    await act(() => new Promise((resolve) => setTimeout(resolve, 20)));
    absent();
  });

  test.each([
    ["a request that waits", broadcastIn("REQUESTED")],
    ["an approved request", broadcastIn("APPROVED")],
    ["a refused request", broadcastIn("REFUSED")],
  ])("%s of somebody else, as an admin is shown it", async (_what, broadcast) => {
    const { api } = renderSection(
      marketBroadcast({ broadcast, viewer: broadcastViewer({ can_watch: false }) }),
    );

    await waitFor(() => expect(api.calls(BROADCAST)).toHaveLength(1));
    await act(() => new Promise((resolve) => setTimeout(resolve, 20)));
    absent();
  });
});

describe("for every viewer", () => {
  test("the section is a card under the heading Live stream, with the Live badge while it is on", async () => {
    renderSection(ON_AIR);

    const heading = await shown();
    expect(heading).toHaveClass("type-h3");
    expect(section()).toHaveAccessibleName("Live stream");
    expect(section()!.querySelector("[data-slot=card]")).not.toBeNull();
    expect(badge()).toHaveTextContent("Live");
    expect(heading.parentElement).toContainElement(badge() as HTMLElement);
  });

  test("LIVE and not on air: the live stream is starting", async () => {
    renderSection(STARTING);
    await shown();

    const line = card().getByRole("status");
    expect(line).toHaveTextContent("The live stream is starting.");
    expect(line).toHaveClass("type-body-sm", "text-muted-foreground");
    expect(badge()).toHaveTextContent("Live");
    expect(card().queryByRole("button")).not.toBeInTheDocument();
    expect(video()).not.toBeInTheDocument();
  });

  test("LIVE and the viewer may watch: the sentence and Watch live, and no video before the tap", async () => {
    const { api } = renderSection(ON_AIR);
    await shown();

    expect(card().getByText("A live stream is on for this market.")).toBeInTheDocument();
    expect(card().getByRole("button", { name: "Watch live" })).toHaveAttribute(
      "data-variant",
      "secondary",
    );
    // Video starts on a tap and never by itself: no element, no player, no request.
    expect(document.querySelector("video")).toBeNull();
    expect(FakeHls.instances).toEqual([]);
    expect(api.requests).toHaveLength(1);
    expect(card().queryByText("Live, a few seconds behind.")).not.toBeInTheDocument();
  });

  test("Watch live plays the playlist of the live stream in the video box", async () => {
    setViewport("wide");
    const { user } = renderSection(ON_AIR);
    await shown();

    await user.click(card().getByRole("button", { name: "Watch live" }));

    expect(video()).toBeInTheDocument();
    await waitFor(() => expect(FakeHls.instances).toHaveLength(1));
    expect(FakeHls.latest().source).toBe(PLAYLIST);
    expect(card().getByText("Live, a few seconds behind.")).toHaveClass("type-caption");
    expect(card().getByRole("button", { name: "Stop watching" })).toHaveAttribute(
      "data-variant",
      "ghost",
    );
    expect(card().queryByRole("button", { name: "Watch live" })).not.toBeInTheDocument();
    expect(card().queryByText("A live stream is on for this market.")).not.toBeInTheDocument();
  });

  test("Stop watching takes the player away and brings Watch live back", async () => {
    setViewport("wide");
    const { user } = renderSection(ON_AIR);
    await shown();
    await user.click(card().getByRole("button", { name: "Watch live" }));
    await waitFor(() => expect(FakeHls.instances).toHaveLength(1));
    const player = FakeHls.latest();

    await user.click(card().getByRole("button", { name: "Stop watching" }));

    expect(document.querySelector("video")).toBeNull();
    expect(player.destroyed).toBe(true);
    expect(card().getByRole("button", { name: "Watch live" })).toBeInTheDocument();
    expect(card().queryByText("Live, a few seconds behind.")).not.toBeInTheDocument();
  });

  test("LIVE and full: the sentence, and Try again reads the answer again", async () => {
    const { api, user } = renderSection(FULL);
    await shown();

    expect(
      card().getByText("The live stream is full right now. Try again in a moment."),
    ).toBeInTheDocument();
    expect(card().queryByRole("button", { name: "Watch live" })).not.toBeInTheDocument();
    expect(badge()).toHaveTextContent("Live");

    api.on(BROADCAST, json(200, ON_AIR));
    await user.click(card().getByRole("button", { name: "Try again" }));

    expect(await card().findByRole("button", { name: "Watch live" })).toBeInTheDocument();
    expect(api.calls(BROADCAST)).toHaveLength(2);
    expect(card().queryByText(/is full right now/)).not.toBeInTheDocument();
    // A place that is free is no tap on Watch live.
    expect(document.querySelector("video")).toBeNull();
  });

  test("a viewer who watched and lost the place gets it back with Try again", async () => {
    setViewport("wide");
    const { api, client, user } = renderSection(ON_AIR);
    await shown();
    await user.click(card().getByRole("button", { name: "Watch live" }));
    await waitFor(() => expect(FakeHls.instances).toHaveLength(1));

    await becomes(api, client, FULL);
    await card().findByText(/is full right now/);
    expect(document.querySelector("video")).toBeNull();
    expect(FakeHls.latest().destroyed).toBe(true);

    api.on(BROADCAST, json(200, ON_AIR));
    await user.click(card().getByRole("button", { name: "Try again" }));

    // The viewer asked for this live stream: it plays again.
    await waitFor(() => expect(video()).toBeInTheDocument());
    await waitFor(() => expect(FakeHls.instances).toHaveLength(2));
  });

  test("it was LIVE while the screen was open and the answer says otherwise: it has ended", async () => {
    const { api, client } = renderSection(ON_AIR);
    await shown();

    await becomes(api, client, OVER);

    const line = await card().findByRole("status");
    expect(line).toHaveTextContent("The live stream has ended.");
    expect(badge()).toBeNull();
    expect(card().queryByRole("button")).not.toBeInTheDocument();
    expect(chat()).not.toBeInTheDocument();
  });

  test("the end takes the player of a viewer who watches away", async () => {
    setViewport("wide");
    const { api, client, user } = renderSection(ON_AIR);
    await shown();
    await user.click(card().getByRole("button", { name: "Watch live" }));
    await waitFor(() => expect(FakeHls.instances).toHaveLength(1));

    await becomes(
      api,
      client,
      marketBroadcast({
        broadcast: broadcastIn("ENDED", { end_reason: "ADMIN" }),
        viewer: broadcastViewer({ can_watch: false }),
      }),
    );

    expect(await card().findByText("The live stream has ended.")).toBeInTheDocument();
    expect(document.querySelector("video")).toBeNull();
    expect(FakeHls.latest().destroyed).toBe(true);
    expect(card().queryByRole("button", { name: "Stop watching" })).not.toBeInTheDocument();
  });

  test("a live stream that starts later on the same market waits for a tap of its own", async () => {
    setViewport("wide");
    const { api, client, user } = renderSection(ON_AIR);
    await shown();
    await user.click(card().getByRole("button", { name: "Watch live" }));
    await waitFor(() => expect(FakeHls.instances).toHaveLength(1));

    const later = "0b2d4f6a-1c3e-4a5b-8d7f-9e1a3c5b7d9f";
    await becomes(
      api,
      client,
      marketBroadcast({
        broadcast: broadcastIn("LIVE", {
          id: later,
          playlist_url: `/api/broadcasts/${later}/hls/index.m3u8`,
        }),
      }),
    );

    expect(await card().findByRole("button", { name: "Watch live" })).toBeInTheDocument();
    expect(document.querySelector("video")).toBeNull();
    expect(FakeHls.instances).toHaveLength(1);
    expect(card().queryByText("The live stream has ended.")).not.toBeInTheDocument();
  });

  test("a player that fails asks the API, and the section shows what the answer says", async () => {
    setViewport("wide");
    const { api, user } = renderSection(ON_AIR);
    await shown();
    await user.click(card().getByRole("button", { name: "Watch live" }));
    await waitFor(() => expect(FakeHls.instances).toHaveLength(1));
    showFrame(video()!, 2);

    // The proxy refuses the playlist of a live stream that is over: the player fails.
    api.on(BROADCAST, json(200, OVER));
    act(() => FakeHls.latest().fail());

    expect(await card().findByText("The live stream has ended.")).toBeInTheDocument();
    expect(api.calls(BROADCAST)).toHaveLength(2);
    expect(document.querySelector("video")).toBeNull();
    // No sentence of the proxy and no banner: the section says it.
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("live", () => {
  const live = { live: true, config: config({ live_announce_spread_ms: 0 }) };

  test("the section names the live stream of its market to the event stream", async () => {
    const streams = installFakeEventSource();
    renderSection(STARTING, {}, live);
    await shown();

    await waitFor(() => expect(streams.latest().topics).toEqual([`broadcast:${MARKET_ID}`]));
  });

  test("broadcast.changed of its market reads the answer again: starting turns into Watch live", async () => {
    const streams = installFakeEventSource();
    const { api } = renderSection(STARTING, {}, live);
    await shown();
    await waitFor(() => expect(streams.instances.length).toBeGreaterThan(0));
    act(() => streams.latest().open());
    act(() => streams.latest().send(readyEvent()));
    // The read of the mount, and the one that follows the stream's opening.
    await waitFor(() => expect(api.calls(BROADCAST)).toHaveLength(2));

    api.on(BROADCAST, json(200, ON_AIR));
    act(() => streams.latest().send(broadcastChangedEvent()));

    expect(await card().findByRole("button", { name: "Watch live" })).toBeInTheDocument();
    expect(api.calls(BROADCAST)).toHaveLength(3);
    expect(card().queryByText("The live stream is starting.")).not.toBeInTheDocument();
  });

  test("broadcast.changed of another market reads nothing", async () => {
    const streams = installFakeEventSource();
    const { api } = renderSection(STARTING, {}, live);
    await shown();
    await waitFor(() => expect(streams.instances.length).toBeGreaterThan(0));
    act(() => streams.latest().open());
    act(() => streams.latest().send(readyEvent()));
    await waitFor(() => expect(api.calls(BROADCAST)).toHaveLength(2));

    act(() => streams.latest().send(broadcastChangedEvent({ market_id: OTHER_MARKET_ID })));
    await act(() => new Promise((resolve) => setTimeout(resolve, 30)));

    expect(api.calls(BROADCAST)).toHaveLength(2);
    expect(card().getByText("The live stream is starting.")).toBeInTheDocument();
  });

  test("a viewer learns from the event that the live stream ended", async () => {
    const streams = installFakeEventSource();
    const { api } = renderSection(ON_AIR, {}, live);
    await shown();
    await waitFor(() => expect(streams.instances.length).toBeGreaterThan(0));
    act(() => streams.latest().open());
    act(() => streams.latest().send(readyEvent()));
    await waitFor(() => expect(api.calls(BROADCAST)).toHaveLength(2));

    api.on(BROADCAST, json(200, OVER));
    act(() => streams.latest().send(broadcastChangedEvent()));

    expect(await card().findByText("The live stream has ended.")).toBeInTheDocument();
  });
});

describe("the live chat", () => {
  test.each([
    ["on air", ON_AIR],
    ["starting", STARTING],
    ["full", FULL],
  ])(
    "it is mounted directly under the section while the live stream is LIVE: %s",
    async (_w, answer) => {
      renderSection(answer);
      await shown();

      expect(chat()).toHaveAttribute("data-market", MARKET_ID);
      expect(chat()).toHaveAttribute("data-broadcast", BROADCAST_ID);
      expect(chat()).toHaveAttribute("data-fill", "no");
      // Under the card, not in it.
      expect(section()).not.toContainElement(chat());
      expect(
        section()!.compareDocumentPosition(chat()!) & Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
    },
  );

  test.each([
    ["a request", own("REQUESTED")],
    ["an approved request", own("APPROVED", { can_start: true })],
    ["a refused request", own("REFUSED", { can_request: true })],
    ["a live stream that ended", own("ENDED", { can_request: true })],
    ["no live stream", noBroadcast({ can_request: true })],
  ])("it is not mounted for %s", async (_what, answer) => {
    renderSection(answer);
    await shown();

    expect(chat()).not.toBeInTheDocument();
  });

  test("it is taken away when the live stream is over", async () => {
    const { api, client } = renderSection(ON_AIR);
    await shown();
    expect(chat()).toBeInTheDocument();

    await becomes(api, client, OVER);

    await waitFor(() => expect(chat()).not.toBeInTheDocument());
  });

  test("it stays the same panel when its viewer starts and stops watching", async () => {
    const { user } = renderSection(ON_AIR);
    await shown();
    const field = screen.getByLabelText("Chat stand-in field");
    await user.type(field, "hello");

    await user.click(card().getByRole("button", { name: "Watch live" }));
    await waitFor(() => expect(FakeHls.instances).toHaveLength(1));
    await user.click(card().getByRole("button", { name: "Stop watching" }));

    // What was typed is still there: the panel was not mounted again.
    expect(screen.getByLabelText("Chat stand-in field")).toBe(field);
    expect(field).toHaveValue("hello");
  });
});

describe("for the proposer, and for whoever streams", () => {
  const REQUESTED = own("REQUESTED", { can_watch: false });

  test("the proposer without a live stream is offered the request, with the box not ticked", async () => {
    renderSection(noBroadcast({ can_request: true }));
    await shown();

    expect(
      card().getByText(
        "Ask VIS to let you stream this market live, from your camera. An admin decides on every request.",
      ),
    ).toBeInTheDocument();
    expect(card().getByRole("checkbox", { name: "I accept the usage policy" })).not.toBeChecked();
    expect(card().getByRole("link", { name: "usage policy" })).toHaveAttribute("href", "/policy");
    const button = card().getByRole("button", { name: "Request a live stream" });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("data-variant", "secondary");
    expect(badge()).toBeNull();
  });

  test("the request is sent with policy_accepted: true, is told with a toast, and is in review", async () => {
    const { api, user } = renderSection(noBroadcast({ can_request: true }));
    api.on(REQUEST, json(201, REQUESTED));
    await shown();

    await user.click(card().getByRole("checkbox", { name: "I accept the usage policy" }));
    await user.click(card().getByRole("button", { name: "Request a live stream" }));

    expect(await screen.findByText("Request sent. VIS decides on it.")).toBeInTheDocument();
    expect(api.calls(REQUEST)).toHaveLength(1);
    expect(api.lastCall(REQUEST)?.body).toEqual({ policy_accepted: true });
    expect(api.lastCall(REQUEST)?.path).toBe(`/api/markets/${MARKET_ID}/broadcast/request`);
    // The answer is the live stream of the market: no read of its own follows.
    expect(
      await card().findByText("You asked for a live stream. VIS decides on it."),
    ).toBeInTheDocument();
    expect(card().queryByRole("button", { name: "Request a live stream" })).not.toBeInTheDocument();
    expect(api.calls(BROADCAST)).toHaveLength(1);
  });

  test("a refusal that names no field stands above the button, with the API's sentence", async () => {
    const { api, user } = renderSection(noBroadcast({ can_request: true }));
    const sentence = "You have reached today's limit of live stream requests. Try again tomorrow.";
    api.on(REQUEST, apiError(409, "LIMIT_EXCEEDED", sentence, { limit: 3 }));
    await shown();

    await user.click(card().getByRole("checkbox", { name: "I accept the usage policy" }));
    await user.click(card().getByRole("button", { name: "Request a live stream" }));

    const refusal = await card().findByText(sentence);
    expect(refusal.closest("[data-slot=submit-error]")).not.toBeNull();
    const button = card().getByRole("button", { name: "Request a live stream" });
    expect(refusal.compareDocumentPosition(button) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // The form stays, and what was ticked with it.
    expect(card().getByRole("checkbox", { name: "I accept the usage policy" })).toBeChecked();
  });

  test("a refusal that names policy_accepted stands under the checkbox", async () => {
    const { api, user } = renderSection(noBroadcast({ can_request: true }));
    const sentence = "Accept the usage policy to continue.";
    api.on(REQUEST, apiError(422, "VALIDATION_ERROR", sentence, { field: "policy_accepted" }));
    await shown();

    await user.click(card().getByRole("checkbox", { name: "I accept the usage policy" }));
    await user.click(card().getByRole("button", { name: "Request a live stream" }));

    const refusal = await card().findByText(sentence);
    expect(refusal.closest("[data-slot=checkbox-field]")).not.toBeNull();
    expect(section()!.querySelector("[data-slot=submit-error]")).toBeNull();
    expect(card().getByRole("checkbox", { name: "I accept the usage policy" })).toBeInvalid();
  });

  test("REQUESTED: the badge In review and the sentence", async () => {
    renderSection(REQUESTED);
    await shown();

    expect(badge()).toHaveTextContent("In review");
    expect(card().getByText("You asked for a live stream. VIS decides on it.")).toBeInTheDocument();
    expect(card().queryByRole("button")).not.toBeInTheDocument();
    expect(card().queryByRole("link")).not.toBeInTheDocument();
  });

  test("APPROVED: the sentence and the way to the studio", async () => {
    renderSection(own("APPROVED", { can_watch: false, can_start: true }));
    await shown();

    expect(
      card().getByText("Your live stream is approved. Start it when you are ready."),
    ).toBeInTheDocument();
    const link = card().getByRole("link", { name: "Set up the live stream" });
    expect(link).toHaveAttribute("href", STUDIO);
    expect(link).toHaveAttribute("data-variant", "secondary");
  });

  test("APPROVED on a market that cannot be streamed: no way to the studio", async () => {
    renderSection(own("APPROVED", { can_watch: false, can_start: false }));
    await shown();

    expect(card().queryByRole("link", { name: "Set up the live stream" })).not.toBeInTheDocument();
  });

  test("REFUSED: the admin's reason, then the request again", async () => {
    renderSection(own("REFUSED", { can_watch: false, can_request: true }));
    await shown();

    expect(card().getByText("Your request was not approved.")).toBeInTheDocument();
    expect(card().getByText("The market closes before the event starts.")).toBeInTheDocument();
    expect(card().getByRole("button", { name: "Request a live stream" })).toBeDisabled();
    expect(card().getByRole("checkbox", { name: "I accept the usage policy" })).not.toBeChecked();
  });

  test("REFUSED without a way to ask again: no request", async () => {
    renderSection(own("REFUSED", { can_watch: false, can_request: false }));
    await shown();

    expect(card().getByText("Your request was not approved.")).toBeInTheDocument();
    expect(card().queryByRole("button", { name: "Request a live stream" })).not.toBeInTheDocument();
  });

  test("LIVE: You are live, the way to the studio, and the rows of every viewer", async () => {
    renderSection(own("LIVE", { can_end: true }));
    await shown();

    expect(card().getByText("You are live.")).toBeInTheDocument();
    expect(card().getByRole("link", { name: "Open the studio" })).toHaveAttribute("href", STUDIO);
    expect(badge()).toHaveTextContent("Live");
    expect(card().getByRole("button", { name: "Watch live" })).toBeInTheDocument();
  });

  test("ENDED by itself, and the server lets it go on: the reason, and the way to the studio", async () => {
    renderSection(own("ENDED", { can_start: true }, { end_reason: "STALLED" }));
    await shown();

    expect(
      card().getByText(
        "Your live stream has ended. No video arrived for 120 seconds. You can go live again without asking.",
      ),
    ).toBeInTheDocument();
    expect(card().getByRole("link", { name: "Set up the live stream" })).toHaveAttribute(
      "href",
      STUDIO,
    );
    expect(card().queryByRole("button", { name: "Request a live stream" })).not.toBeInTheDocument();
  });

  test("a live stream that may go on and may be asked for again offers the way that asks nobody", async () => {
    // After an end by itself the server says both: going on needs no admin, asking does.
    renderSection(
      own("ENDED", { can_start: true, can_request: true }, { end_reason: "TIME_LIMIT" }),
    );
    await shown();

    expect(
      card().getByText(
        "Your live stream has ended. It reached the limit of 120 minutes. You can go live again without asking.",
      ),
    ).toBeInTheDocument();
    expect(card().getByRole("link", { name: "Set up the live stream" })).toBeInTheDocument();
    expect(card().queryByRole("button", { name: "Request a live stream" })).not.toBeInTheDocument();
    expect(card().queryByRole("checkbox")).not.toBeInTheDocument();
  });

  test.each([
    ["TIME_LIMIT", "Your live stream has ended. It reached the limit of 120 minutes."],
    ["ADMIN", "Your live stream has ended. VIS ended it."],
    ["BROADCASTER", "Your live stream has ended."],
  ] as const)("ENDED by %s: its sentence, then the request again", async (end_reason, sentence) => {
    renderSection(own("ENDED", { can_request: true }, { end_reason }));
    await shown();

    expect(card().getByText(sentence)).toBeInTheDocument();
    expect(card().getByRole("button", { name: "Request a live stream" })).toBeInTheDocument();
    expect(card().queryByRole("link", { name: "Set up the live stream" })).not.toBeInTheDocument();
  });

  test("the two figures of an end reason are those of /config, with their noun", async () => {
    renderSection(
      own("ENDED", {}, { end_reason: "STALLED" }),
      {},
      {
        config: config({ broadcast: { ...config().broadcast, stall_seconds: 1 } }),
      },
    );
    await shown();

    expect(
      card().getByText("Your live stream has ended. No video arrived for 1 second."),
    ).toBeInTheDocument();
  });

  test("a live stream of the viewer's own that ends in front of them is said once", async () => {
    const { api, client } = renderSection(own("LIVE", { can_end: true }));
    await shown();

    await becomes(api, client, own("ENDED", { can_request: true }, { end_reason: "ADMIN" }));

    expect(
      await card().findByText("Your live stream has ended. VIS ended it."),
    ).toBeInTheDocument();
    expect(card().queryByText("The live stream has ended.")).not.toBeInTheDocument();
  });

  test("an approval reaches the page of the proposer without a reload", async () => {
    const { api, client } = renderSection(REQUESTED);
    await shown();

    await becomes(api, client, own("APPROVED", { can_watch: false, can_start: true }));

    expect(await card().findByRole("link", { name: "Set up the live stream" })).toBeInTheDocument();
    expect(badge()).toBeNull();
  });
});

describe("watching on a phone", () => {
  const box = () => document.querySelector<HTMLElement>("[data-slot=live-stream-box]");
  const rest = () => section()!.parentElement!;

  async function watch(props: Partial<MarketBroadcastProps> = {}) {
    const scrolled = vi.spyOn(Element.prototype, "scrollIntoView");
    const view = renderSection(ON_AIR, props);
    await shown();
    await view.user.click(card().getByRole("button", { name: "Watch live" }));
    await waitFor(() => expect(FakeHls.instances).toHaveLength(1));
    return { ...view, scrolled };
  }

  test("the video box leaves the card, heads the column and sticks under the top bar", async () => {
    await watch();

    expect(box()).toContainElement(video());
    expect(section()).not.toContainElement(video());
    expect(box()).toHaveClass("sticky", "top-(--top-bar-space)", "order-first", "bg-background");
    // Held sideways, a phone keeps part of its page in sight under the box.
    expect(document.querySelector("[data-slot=video-box]")).toHaveClass("max-h-[45dvh]");
    // A part of the column like the card: the box sticks at every scroll position of the page.
    expect(box()!.parentElement).toBe(screen.getByTestId("column"));
    expect(rest().parentElement).toBe(screen.getByTestId("column"));
  });

  test("the tab bar is hidden while the viewer watches, and back afterwards", async () => {
    const { user } = await watch();
    expect(screen.getByTestId("tab-bar")).toHaveTextContent("hidden");

    await user.click(card().getByRole("button", { name: "Stop watching" }));

    expect(screen.getByTestId("tab-bar")).toHaveTextContent("shown");
    expect(box()).toBeNull();
  });

  test("the card is one row: the heading with its line, and Stop watching", async () => {
    await watch();

    const heading = card().getByRole("heading", { name: "Live stream" });
    const row = heading.parentElement!.parentElement!.parentElement!;
    expect(row).toContainElement(card().getByText("Live, a few seconds behind."));
    expect(row).toContainElement(card().getByRole("button", { name: "Stop watching" }));
    expect(section()!.querySelector("[data-slot=card]")!.children).toHaveLength(1);
  });

  test("the chat takes the height that is left, and never less than 200 px", async () => {
    await watch();

    expect(chat()).toHaveAttribute("data-fill", "yes");
    expect(rest()).toHaveClass("grid", "grid-rows-[auto_minmax(12.5rem,1fr)]");
    // What is left: the screen less the top bar, the room of the tab bar, the box and 16 px.
    expect(rest().style.getPropertyValue("--live-stream-box")).toBe(
      "min((100vw - 2rem) * 9 / 16, 45dvh)",
    );
    expect(rest().style.minHeight).toBe(
      "calc(100dvh - var(--top-bar-space) - var(--tab-bar-space) - var(--live-stream-box) - 1rem)",
    );
    // The chat fills its row without adding to its height.
    const row = document.querySelector<HTMLElement>("[data-slot=live-stream-chat]")!;
    expect(row).toHaveClass("relative");
    expect(row.firstElementChild).toHaveClass("absolute", "inset-0", "flex", "flex-col");
  });

  test("where the screen pins its outcome buttons, the chat ends above their 73 px", async () => {
    await watch({ pinnedButtons: true });

    expect(rest().style.minHeight).toBe(
      "calc(100dvh - var(--top-bar-space) - var(--tab-bar-space) - var(--live-stream-box) - 5.5625rem)",
    );
  });

  test("Watch live brings the card right under the box", async () => {
    const { scrolled } = await watch();

    expect(scrolled).toHaveBeenCalledTimes(1);
    expect(scrolled.mock.instances[0]).toBe(rest());
    expect(scrolled).toHaveBeenCalledWith({ block: "start" });
    // Its place when the page is scrolled to it: below the top bar by the box and 8 px.
    expect(rest().style.scrollMarginTop).toBe("calc(var(--live-stream-box) + 0.5rem)");
  });

  test("the screen is told when the viewer starts to watch and when that ends", async () => {
    const onWatchingChange = vi.fn();
    const { user } = await watch({ onWatchingChange });
    expect(onWatchingChange.mock.calls).toEqual([[true]]);

    await user.click(card().getByRole("button", { name: "Stop watching" }));

    expect(onWatchingChange.mock.calls).toEqual([[true], [false]]);
  });

  test("the screen is told when the live stream ends under a viewer who watches", async () => {
    const onWatchingChange = vi.fn();
    const onTypingChange = vi.fn();
    const { api, client, user } = await watch({ onWatchingChange, onTypingChange });
    await user.click(screen.getByLabelText("Chat stand-in field"));
    expect(onTypingChange.mock.calls).toEqual([[true]]);

    await becomes(api, client, OVER);
    await card().findByText("The live stream has ended.");

    expect(onWatchingChange.mock.calls).toEqual([[true], [false]]);
    // The composer went with its chat and said nothing: the section says it.
    expect(onTypingChange).toHaveBeenLastCalledWith(false);
    expect(screen.getByTestId("tab-bar")).toHaveTextContent("shown");
  });

  test("the screen is told when the composer of the chat takes the focus and loses it", async () => {
    const onTypingChange = vi.fn();
    const { user } = await watch({ onTypingChange });

    await user.click(screen.getByLabelText("Chat stand-in field"));
    await user.tab();

    expect(onTypingChange.mock.calls).toEqual([[true], [false]]);
  });

  test("the screen is told when the section goes while its viewer watches", async () => {
    const onWatchingChange = vi.fn();
    const { unmount } = await watch({ onWatchingChange });

    unmount();

    expect(onWatchingChange.mock.calls).toEqual([[true], [false]]);
  });

  test("not watching, nothing sticks and nothing is hidden", async () => {
    renderSection(ON_AIR);
    await shown();

    expect(box()).toBeNull();
    expect(screen.getByTestId("tab-bar")).toHaveTextContent("shown");
    expect(rest()).toHaveClass("flex", "flex-col", "gap-3");
    expect(rest().style.minHeight).toBe("");
    expect(chat()).toHaveAttribute("data-fill", "no");
  });
});

describe("watching from 768 px", () => {
  test("the box stands in the card, nothing sticks and the tab bar stays", async () => {
    setViewport("wide");
    const scrolled = vi.spyOn(Element.prototype, "scrollIntoView");
    const onWatchingChange = vi.fn();
    const { user } = renderSection(ON_AIR, { onWatchingChange, pinnedButtons: true });
    await shown();

    await user.click(card().getByRole("button", { name: "Watch live" }));
    await waitFor(() => expect(FakeHls.instances).toHaveLength(1));

    expect(section()).toContainElement(video());
    expect(document.querySelector("[data-slot=live-stream-box]")).toBeNull();
    expect(document.querySelector("[data-slot=video-box]")).not.toHaveClass("max-h-[45dvh]");
    expect(screen.getByTestId("tab-bar")).toHaveTextContent("shown");
    expect(chat()).toHaveAttribute("data-fill", "no");
    expect(section()!.parentElement!.style.minHeight).toBe("");
    expect(scrolled).not.toHaveBeenCalled();
    // The screen is told at every width: what it does about it is its own.
    expect(onWatchingChange.mock.calls).toEqual([[true]]);
  });
});
