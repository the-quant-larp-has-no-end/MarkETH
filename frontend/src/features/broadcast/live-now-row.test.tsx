import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { installFakeEventSource } from "@/test/fake-event-source";
import {
  broadcastChangedEvent,
  config,
  liveBroadcast,
  liveBroadcasts,
  MARKET_ID,
  OTHER_MARKET_ID,
  readyEvent,
} from "@/test/fixtures";
import { apiError, deferred, json, mockApi, noAnswer, type Route } from "@/test/http";
import { renderWithProviders } from "@/test/render";

import { LiveNowRow } from "./live-now-row";

const LIVE: Route = "GET /api/broadcasts/live";

const QUESTION = liveBroadcast().question;
const OTHER = liveBroadcast({
  broadcast_id: "0b2d4f6a-1c3e-4a5b-8d7f-9e1a3c5b7d9f",
  market_id: OTHER_MARKET_ID,
  question:
    "Will the ASVZ run sell out before noon on the day it opens for registration this year?",
});

const list = () => document.querySelector<HTMLElement>("[data-slot=live-now]");

/** The row in a column of its own, so that a test sees whether anything stands there. */
const ROW = (
  <div data-testid="column">
    <LiveNowRow />
  </div>
);

describe("a row per live stream", () => {
  test("one link with the Live badge, the question and a chevron, named Live now", async () => {
    mockApi({ [LIVE]: json(200, liveBroadcasts()) });
    renderWithProviders(ROW);

    const link = await screen.findByRole("link", { name: `Live now: ${QUESTION}` });
    expect(link).toHaveAttribute("href", `/markets/${MARKET_ID}`);
    expect(link.querySelector("[data-slot=badge]")).toHaveTextContent("Live");
    expect(within(link).getByText(QUESTION)).toHaveClass("type-h3", "truncate");
    expect(link.querySelector("svg.lucide-chevron-right")).toHaveAttribute("aria-hidden", "true");
    // One line, 44 px tall, a card as wide as the content column.
    expect(link).toHaveClass("h-11", "rounded-lg", "border", "bg-card", "px-4");
    // No picture of a live stream stands in a row.
    expect(link.querySelector("img, video")).toBeNull();
  });

  test("the rows stand in the order the API returns them, 8 px apart", async () => {
    mockApi({ [LIVE]: json(200, liveBroadcasts({ items: [OTHER, liveBroadcast()] })) });
    renderWithProviders(ROW);

    const links = await screen.findAllByRole("link");
    expect(links.map((link) => link.getAttribute("href"))).toEqual([
      `/markets/${OTHER_MARKET_ID}`,
      `/markets/${MARKET_ID}`,
    ]);
    expect(links[0]).toHaveAccessibleName(`Live now: ${OTHER.question}`);
    expect(list()).toHaveClass("flex", "flex-col", "gap-2");
    expect(within(list()!).getAllByRole("listitem")).toHaveLength(2);
  });
});

describe("the row has content or is absent", () => {
  /** Nothing is shown and nothing is said: no skeleton, no banner, no sentence. */
  const absent = () => {
    // The row stands alone in the column of the test: the column is empty.
    expect(screen.getByTestId("column")).toBeEmptyDOMElement();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  };

  test("while the read loads", async () => {
    const answer = deferred();
    const api = mockApi({ [LIVE]: answer.handler });
    renderWithProviders(ROW);

    await waitFor(() => expect(api.calls(LIVE)).toHaveLength(1));
    absent();

    answer.resolve(json(200, liveBroadcasts()));
    expect(await screen.findByRole("link")).toBeInTheDocument();
  });

  test("when no live stream is on", async () => {
    const api = mockApi({ [LIVE]: json(200, liveBroadcasts({ items: [] })) });
    renderWithProviders(ROW);

    await waitFor(() => expect(api.calls(LIVE)).toHaveLength(1));
    await act(() => new Promise((resolve) => setTimeout(resolve, 20)));
    absent();
  });

  test.each([
    ["an error of the server", () => apiError(500, "INTERNAL_ERROR", "Something went wrong.")],
    ["no answer", noAnswer],
  ])("after %s", async (_what, answer) => {
    const api = mockApi({ [LIVE]: answer });
    renderWithProviders(ROW);

    await waitFor(() => expect(api.calls(LIVE)).toHaveLength(1));
    await act(() => new Promise((resolve) => setTimeout(resolve, 20)));
    absent();
  });
});

describe("live", () => {
  const live = { live: true, config: config({ live_announce_spread_ms: 0 }) };

  async function opened() {
    const streams = installFakeEventSource();
    const api = mockApi({ [LIVE]: json(200, liveBroadcasts({ items: [] })) });
    const view = renderWithProviders(ROW, live);
    await waitFor(() => expect(streams.instances.length).toBeGreaterThan(0));
    act(() => streams.latest().open());
    act(() => streams.latest().send(readyEvent()));
    // The read of the mount, and the one that follows the stream's opening.
    await waitFor(() => expect(api.calls(LIVE)).toHaveLength(2));
    return { ...view, api, streams };
  }

  test("it names the markets to the event stream", async () => {
    const { streams } = await opened();

    expect(streams.latest().topics).toEqual(["markets"]);
  });

  test("a live stream that starts appears, and one that ends leaves", async () => {
    const { api, streams } = await opened();
    expect(list()).toBeNull();

    api.on(LIVE, json(200, liveBroadcasts()));
    act(() => streams.latest().send(broadcastChangedEvent()));

    expect(await screen.findByRole("link", { name: `Live now: ${QUESTION}` })).toBeInTheDocument();
    expect(api.calls(LIVE)).toHaveLength(3);

    api.on(LIVE, json(200, liveBroadcasts({ items: [] })));
    act(() => streams.latest().send(broadcastChangedEvent()));

    await waitFor(() => expect(list()).toBeNull());
    expect(api.calls(LIVE)).toHaveLength(4);
  });

  test("a read that fails afterwards leaves the rows that were shown", async () => {
    const { api, streams } = await opened();
    api.on(LIVE, json(200, liveBroadcasts()));
    act(() => streams.latest().send(broadcastChangedEvent()));
    await screen.findByRole("link");

    api.on(LIVE, () => apiError(500, "INTERNAL_ERROR", "Something went wrong."));
    act(() => streams.latest().send(broadcastChangedEvent()));
    await waitFor(() => expect(api.calls(LIVE)).toHaveLength(4));
    await act(() => new Promise((resolve) => setTimeout(resolve, 20)));

    expect(screen.getByRole("link", { name: `Live now: ${QUESTION}` })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
