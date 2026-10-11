import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { installFakeEventSource } from "@/test/fake-event-source";
import {
  adminBroadcast,
  adminBroadcasts,
  broadcastChangedEvent,
  broadcastIn,
  broadcastPerson,
  config,
  MARKET_ID,
  me,
  OTHER_MARKET_ID,
  readyEvent,
} from "@/test/fixtures";
import { apiError, deferred, json, mockApi, type Route } from "@/test/http";
import { navigation } from "@/test/navigation";
import { renderWithProviders } from "@/test/render";

import { BroadcastQueue } from "./broadcast-queue";

const QUEUE: Route = "GET /api/admin/broadcasts";
const QUESTION = "Will the Polybahn run without interruption during the exam session?";
const OTHER_QUESTION = "Will the ASVZ open a second climbing wall before the end of the semester?";
const NOTHING = { items: [] };

const admin = { me: me({ role: "ADMIN" }) };

/** A stream that is approved and not started, in another market, asked for by somebody else. */
const approved = adminBroadcast({
  ...broadcastIn("APPROVED"),
  id: "0b7d5c3e-1f2a-4b6c-8d9e-7a5b3c1d9e0f",
  market_id: OTHER_MARKET_ID,
  question: OTHER_QUESTION,
  requested_at: "2026-10-11T08:40:00Z",
  requested_by: broadcastPerson({ username: "polyterrasse", email: "carl.muster@student.ethz.ch" }),
});

const section = () => document.querySelector("[data-slot=broadcast-queue]");
const table = () => screen.findByRole("table", { name: "Live streams" });
const cells = (row: HTMLElement) =>
  within(row)
    .getAllByRole("cell")
    .map((cell) => cell.textContent);

describe("Admin: the live streams that wait or are on", () => {
  test("a table headed Live streams: the market, the word for the status, who asked and when, in the API's order", async () => {
    mockApi({ [QUEUE]: json(200, adminBroadcasts({ items: [adminBroadcast(), approved] })) });
    renderWithProviders(<BroadcastQueue />, admin);

    const queue = await table();
    expect(screen.getByRole("heading", { level: 2, name: "Live streams" })).toBeInTheDocument();
    expect(
      within(queue)
        .getAllByRole("columnheader")
        .map((header) => header.textContent),
    ).toEqual(["Market", "Status", "Requested by", "Requested"]);

    const [, first, second] = within(queue).getAllByRole("row");
    // The times stand in the timezone of /config, whatever the device is set to.
    expect(cells(first!)).toEqual([
      QUESTION,
      "Requested",
      "cab_coffee bea.beispiel@ethz.ch",
      "11 Oct 2026, 10:30",
    ]);
    expect(cells(second!)).toEqual([
      OTHER_QUESTION,
      "Approved",
      "polyterrasse carl.muster@student.ethz.ch",
      "11 Oct 2026, 10:40",
    ]);
    // A status is a key: no row shows one.
    expect(queue).not.toHaveTextContent(/REQUESTED|APPROVED/);
  });

  test("a live stream that is on carries the Live badge", async () => {
    const live = adminBroadcast({ ...broadcastIn("LIVE"), requested_by: broadcastPerson() });
    mockApi({ [QUEUE]: json(200, adminBroadcasts({ items: [live] })) });
    renderWithProviders(<BroadcastQueue />, admin);

    const [, row] = within(await table()).getAllByRole("row");
    const badge = within(row!).getByText("Live");
    expect(badge).toHaveAttribute("data-slot", "badge");
    // The table says nothing of how many watch.
    expect(row).not.toHaveTextContent(/watching|viewers/i);
  });

  test("a row opens the admin view of its market", async () => {
    navigation.go("/admin");
    mockApi({ [QUEUE]: json(200, adminBroadcasts({ items: [adminBroadcast(), approved] })) });
    const { user } = renderWithProviders(<BroadcastQueue />, admin);

    const queue = await table();
    expect(within(queue).getByRole("link", { name: QUESTION })).toHaveAttribute(
      "href",
      `/admin/markets/${MARKET_ID}`,
    );
    expect(within(queue).getByRole("link", { name: OTHER_QUESTION })).toHaveAttribute(
      "href",
      `/admin/markets/${OTHER_MARKET_ID}`,
    );

    // A tap anywhere on the row does what its link does.
    await user.click(within(queue).getByText("Approved"));
    expect(navigation.pathname).toBe(`/admin/markets/${OTHER_MARKET_ID}`);
  });

  test("with no row the table and its heading are absent, and no sentence stands in their place", async () => {
    const api = mockApi({ [QUEUE]: json(200, NOTHING) });
    renderWithProviders(<BroadcastQueue />, admin);

    await waitFor(() => expect(section()).not.toBeInTheDocument());
    expect(api.calls(QUEUE)).toHaveLength(1);
    expect(screen.queryByRole("heading")).not.toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  test("while the list loads it shows its heading and a skeleton row", async () => {
    const answer = deferred();
    mockApi({ [QUEUE]: answer.handler });
    renderWithProviders(<BroadcastQueue />, admin);

    expect(screen.getByRole("heading", { level: 2, name: "Live streams" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Loading");
    expect(screen.queryByRole("table")).not.toBeInTheDocument();

    answer.resolve(json(200, adminBroadcasts()));

    expect(await table()).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  test("a read that failed shows the API's message with Try again, which reads the list again", async () => {
    const api = mockApi({
      [QUEUE]: apiError(500, "INTERNAL_ERROR", "Something went wrong on our side."),
    });
    const { user } = renderWithProviders(<BroadcastQueue />, admin);

    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong on our side.");
    expect(screen.getByRole("heading", { level: 2, name: "Live streams" })).toBeInTheDocument();

    api.on(QUEUE, json(200, adminBroadcasts()));
    await user.click(screen.getByRole("button", { name: "Try again" }));

    expect(await table()).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  test("a row the API names nobody for shows a dash in place of who asked", async () => {
    mockApi({
      [QUEUE]: json(200, adminBroadcasts({ items: [adminBroadcast({ requested_by: null })] })),
    });
    renderWithProviders(<BroadcastQueue />, admin);

    const [, row] = within(await table()).getAllByRole("row");
    expect(cells(row!)[2]).toBe("–none yet");
  });

  test("it names markets to the event stream, and a broadcast.changed brings a request without a reload", async () => {
    const streams = installFakeEventSource();
    const api = mockApi({ [QUEUE]: json(200, NOTHING) });
    renderWithProviders(<BroadcastQueue />, {
      ...admin,
      live: true,
      // An announcement is read at once.
      config: config({ live_announce_spread_ms: 0 }),
    });

    expect(streams.latest().topics).toEqual(["markets"]);
    await waitFor(() => expect(section()).not.toBeInTheDocument());
    // The stream is open: what was pushed before is not sent again, so the list is read once more.
    act(() => streams.latest().send(readyEvent()));
    await waitFor(() => expect(api.calls(QUEUE)).toHaveLength(2));

    // Somebody asks for a live stream.
    api.on(QUEUE, json(200, adminBroadcasts()));
    act(() => streams.latest().send(broadcastChangedEvent()));

    expect(await within(await table()).findByText(QUESTION)).toBeInTheDocument();
    expect(api.calls(QUEUE)).toHaveLength(3);

    // An admin refuses it on the page of its market: the table and its heading go again.
    api.on(QUEUE, json(200, NOTHING));
    act(() => streams.latest().send(broadcastChangedEvent({ ts: "2026-10-11T09:50:00Z" })));

    await waitFor(() => expect(section()).not.toBeInTheDocument());
    // One read for each announcement: the component itself asks for nothing.
    expect(api.calls(QUEUE)).toHaveLength(4);
  });
});
