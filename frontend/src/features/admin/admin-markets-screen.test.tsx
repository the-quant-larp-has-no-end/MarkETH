import { act, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { config, marketSummary, MARKET_ID, me, OTHER_MARKET_ID, TODAY } from "@/test/fixtures";
import { apiError, deferred, json, mockApi, type MockRequest, type Route } from "@/test/http";
import { navigation } from "@/test/navigation";
import { renderWithProviders } from "@/test/render";

import { AdminMarketsScreen } from "./admin-markets-screen";

const MARKETS: Route = "GET /api/markets";
/** Every status a market is reported with: the seven its row keeps, and EXPIRED. */
const EVERY_STATUS = [
  "PROPOSED",
  "EXPIRED",
  "REJECTED",
  "OPEN",
  "HALTED",
  "CLOSED",
  "RESOLVED",
  "VOID",
];

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

const statuses = (request: MockRequest) => request.query.getAll("status");
const isQueue = (request: MockRequest) => statuses(request).join() === "PROPOSED";

/** Answers the queue with `queue` and every other list with `rest`. */
function lists(queue: unknown[], rest: unknown[]) {
  return (request: MockRequest) => json(200, { items: isQueue(request) ? queue : rest });
}

const table = (name: string) => screen.findByRole("table", { name });
const cells = (row: HTMLElement) =>
  within(row)
    .getAllByRole("cell")
    .map((cell) => cell.textContent);

// The screen sets a timer for the earliest close time among its proposals in review: the
// tests stand on one day.
beforeEach(() => {
  vi.useFakeTimers({ now: TODAY, toFake: ["Date"] });
});

afterEach(() => {
  vi.useRealTimers();
});

describe("Admin: markets", () => {
  test("stands under the tabs, with a hidden h1 that names the screen", async () => {
    navigation.go("/admin");
    mockApi({ [MARKETS]: lists([], []) });
    renderWithProviders(<AdminMarketsScreen />, admin);

    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Admin");
    expect(screen.getByRole("link", { name: "Markets" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("heading", { level: 2, name: "Proposals" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "Markets" })).toBeInTheDocument();
    await screen.findByText("No markets.");
  });

  test("asks for the queue with PROPOSED, and for all markets with every status by name", async () => {
    const api = mockApi({ [MARKETS]: lists([proposal], [proposal, open]) });
    renderWithProviders(<AdminMarketsScreen />, admin);

    await within(await table("Markets")).findByText(open.question);
    const asked = api.calls(MARKETS).map(statuses);
    expect(asked).toHaveLength(2);
    expect(asked).toContainEqual(["PROPOSED"]);
    expect(asked).toContainEqual(EVERY_STATUS);
    // No request leaves the status out: that would hide proposals, expired and rejected ones.
    expect(asked.every((sent) => sent.length > 0)).toBe(true);
  });

  test("a proposal whose close time has passed is not in the queue, and is found under Expired", async () => {
    const expired = marketSummary({
      id: "expired",
      question: "Will the Polyball sell out before the first of October?",
      status: "EXPIRED",
      trading_open: false,
      p_yes: 0.5,
      p_no: 0.5,
      volume_cr: "0",
      close_at: "2026-10-01T10:00:00Z",
    });
    // The API lists under PROPOSED what an admin can still decide on, and the rest by name.
    const api = mockApi({
      [MARKETS]: (request) => {
        const wanted = statuses(request);
        return json(200, {
          items: [proposal, expired, open].filter((market) => wanted.includes(market.status)),
        });
      },
    });
    const { user } = renderWithProviders(<AdminMarketsScreen />, admin);

    const queue = await table("Proposals");
    await within(queue).findByText(proposal.question);
    expect(within(queue).queryByText(expired.question)).not.toBeInTheDocument();
    // The queue asked for PROPOSED alone: nothing here asks for the expired ones by the clock.
    expect(api.calls(MARKETS).map(statuses)).toContainEqual(["PROPOSED"]);

    // Among all markets it stands with its badge.
    const all = await table("Markets");
    const row = (await within(all).findByText(expired.question)).closest("tr")!;
    expect(within(row).getByText("Expired")).toHaveAttribute("data-slot", "badge");
    expect(within(row).getByRole("link", { name: expired.question })).toHaveAttribute(
      "href",
      "/admin/markets/expired",
    );

    await user.selectOptions(screen.getByLabelText("Status"), "Expired");
    await waitFor(() => expect(api.calls(MARKETS).map(statuses)).toContainEqual(["EXPIRED"]));
    await waitFor(() =>
      expect(
        within(screen.getByRole("table", { name: "Markets" })).getAllByRole("row"),
      ).toHaveLength(2),
    );
    expect(
      within(screen.getByRole("table", { name: "Markets" })).getByText(expired.question),
    ).toBeInTheDocument();
  });

  test("each choice of the status select sends its one status", async () => {
    const api = mockApi({ [MARKETS]: lists([], [open]) });
    const { user } = renderWithProviders(<AdminMarketsScreen />, admin);
    await within(await table("Markets")).findByText(open.question);

    const select = screen.getByLabelText("Status");
    expect(
      within(select)
        .getAllByRole("option")
        .map((option) => option.textContent),
    ).toEqual([
      "All",
      "In review",
      "Expired",
      "Not approved",
      "Open",
      "Paused",
      "Closed",
      "Resolved",
      "Voided",
    ]);
    expect(select).toHaveValue("ALL");

    await user.selectOptions(select, "Paused");
    await waitFor(() => expect(api.calls(MARKETS).map(statuses)).toContainEqual(["HALTED"]));

    await user.selectOptions(select, "Not approved");
    await waitFor(() => expect(api.calls(MARKETS).map(statuses)).toContainEqual(["REJECTED"]));
  });

  test("a row shows the question, the status badge, the chance, the volume and the close time as the API sent them", async () => {
    mockApi({
      [MARKETS]: lists(
        [proposal],
        [
          marketSummary({ p_yes: 0.548, volume_cr: "1250.5", close_at: "2026-10-14T16:00:00Z" }),
          marketSummary({
            id: "resolved",
            question: "Did the Mensa serve fondue?",
            status: "RESOLVED",
            trading_open: false,
            outcome: "NO",
            p_yes: 0.071,
            volume_cr: "88.999999",
            close_at: "2026-11-30T10:30:00Z",
          }),
        ],
      ),
    });
    renderWithProviders(<AdminMarketsScreen />, admin);

    const markets = await table("Markets");
    await within(markets).findByText("Did the Mensa serve fondue?");
    expect(
      within(markets)
        .getAllByRole("columnheader")
        .map((header) => header.textContent),
    ).toEqual(["Question", "Status", "Chance", "Volume", "Closes"]);
    const [, first, second] = within(markets).getAllByRole("row");
    expect(cells(first!)).toEqual([
      "Will the Polybahn run without interruption during the exam session?",
      "Open",
      "54.8%",
      "1,250.51,250.5 tokens",
      "14 Oct 2026, 18:00",
    ]);
    // The figure is cut, never rounded, and the rows keep the API's order.
    expect(cells(second!)).toEqual([
      "Did the Mensa serve fondue?",
      "Resolved NO",
      "7.1%",
      "88.9988.99 tokens",
      "30 Nov 2026, 11:30",
    ]);

    const [, queued] = within(await table("Proposals")).getAllByRole("row");
    expect(cells(queued!)).toEqual([
      proposal.question,
      "In review",
      "50.0%",
      "00 tokens",
      "11 Dec 2026, 18:00",
    ]);
  });

  test("a row opens the admin view of its market: by its link, and by a tap anywhere on it", async () => {
    mockApi({ [MARKETS]: lists([proposal], [open]) });
    const { user } = renderWithProviders(<AdminMarketsScreen />, admin);

    const link = await within(await table("Markets")).findByRole("link", {
      name: open.question,
    });
    expect(link).toHaveAttribute("href", `/admin/markets/${MARKET_ID}`);
    // A row is 44 px tall, and its link fills it.
    expect(link).toHaveClass("min-h-11");

    await user.click(within(await table("Proposals")).getByText("In review"));
    expect(navigation.pathname).toBe(`/admin/markets/${OTHER_MARKET_ID}`);

    navigation.go("/admin");
    await user.click(link);
    expect(navigation.pathname).toBe(`/admin/markets/${MARKET_ID}`);
    expect(navigation.router.push).toHaveBeenCalledTimes(2);
  });

  test("shows skeleton rows while a list loads", async () => {
    const answer = deferred();
    mockApi({ [MARKETS]: answer.handler });
    renderWithProviders(<AdminMarketsScreen />, admin);

    expect(document.querySelectorAll("[data-slot=table-skeleton]")).toHaveLength(2);
    expect(screen.queryByText("No markets.")).not.toBeInTheDocument();

    answer.resolve(json(200, { items: [] }));
    await waitFor(() =>
      expect(document.querySelector("[data-slot=table-skeleton]")).not.toBeInTheDocument(),
    );
  });

  test("an empty table is one plain sentence", async () => {
    mockApi({ [MARKETS]: lists([], []) });
    renderWithProviders(<AdminMarketsScreen />, admin);

    expect(await screen.findByText("No proposals waiting.")).toBeInTheDocument();
    expect(await screen.findByText("No markets.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  test("a list that cannot be read shows the API's message with Try again, and the other list stays", async () => {
    const api = mockApi({
      [MARKETS]: (request) =>
        isQueue(request)
          ? apiError(
              503,
              "SERVICE_UNAVAILABLE",
              "This is temporarily unavailable. Try again shortly.",
            )
          : json(200, { items: [open] }),
    });
    const { user } = renderWithProviders(<AdminMarketsScreen />, admin);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This is temporarily unavailable. Try again shortly.",
    );
    expect(within(await table("Markets")).getByText(open.question)).toBeInTheDocument();

    api.on(MARKETS, lists([proposal], [open]));
    await user.click(screen.getByRole("button", { name: "Try again" }));

    expect(within(await table("Proposals")).getByText(proposal.question)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  describe("when a proposal in review reaches its close time", () => {
    /** Twenty seconds before the ASVZ proposal closes, at 17:00 UTC. */
    const BEFORE_CLOSE = new Date("2026-12-11T16:59:40Z");

    /** A second proposal in review, which closes a minute after the first. */
    const later = marketSummary({
      ...proposal,
      id: "later",
      question: "Will the CAB get a second cafeteria?",
      close_at: "2026-12-11T17:01:00Z",
    });
    const expired = (market: typeof proposal) =>
      marketSummary({ ...market, status: "EXPIRED", trading_open: false });

    const render = (pollSeconds = 10) =>
      renderWithProviders(<AdminMarketsScreen />, {
        ...admin,
        config: config({ live_poll_fallback_seconds: pollSeconds }),
      });
    const queueReads = (api: ReturnType<typeof mockApi>) => api.calls(MARKETS).filter(isQueue);
    const tableReads = (api: ReturnType<typeof mockApi>) =>
      api.calls(MARKETS).filter((request) => !isQueue(request));
    const inQueue = (question: string) =>
      within(screen.getByRole("table", { name: "Proposals" })).queryByText(question);
    const badgeInTable = (question: string) =>
      within(screen.getByRole("table", { name: "Markets" }))
        .getByText(question)
        .closest("tr")!
        .querySelector("[data-slot=badge]")!.textContent;

    beforeEach(() => {
      vi.useFakeTimers({ now: BEFORE_CLOSE, shouldAdvanceTime: true });
    });

    test("both lists are read again at the earliest close time: the proposal leaves the queue, and the table shows it as the answer reports it", async () => {
      // The later one stands first: neither list is asked to be in any order.
      const api = mockApi({ [MARKETS]: lists([later, proposal], [later, proposal, open]) });
      render();
      await within(await table("Proposals")).findByText(proposal.question);
      await within(await table("Markets")).findByText(open.question);
      expect(badgeInTable(proposal.question)).toBe("In review");
      api.on(MARKETS, lists([later], [later, expired(proposal), open]));

      // Not before its time.
      await act(() => vi.advanceTimersByTimeAsync(15_000));
      expect(api.calls(MARKETS)).toHaveLength(2);

      await act(() => vi.advanceTimersByTimeAsync(5_500));

      await waitFor(() => expect(queueReads(api)).toHaveLength(2));
      await waitFor(() => expect(tableReads(api)).toHaveLength(2));
      // The same lists are asked for: the queue with PROPOSED, the table with its choice.
      expect(api.calls(MARKETS).slice(2).map(statuses)).toEqual(
        expect.arrayContaining([["PROPOSED"], EVERY_STATUS]),
      );
      await waitFor(() => expect(inQueue(proposal.question)).not.toBeInTheDocument());
      expect(inQueue(later.question)).toBeInTheDocument();
      await waitFor(() => expect(badgeInTable(proposal.question)).toBe("Expired"));
      expect(badgeInTable(later.question)).toBe("In review");
    });

    test("after the answer it waits for the earliest close time that is left", async () => {
      const api = mockApi({ [MARKETS]: lists([proposal, later], [proposal, later]) });
      render();
      await within(await table("Proposals")).findByText(proposal.question);
      api.on(MARKETS, lists([later], [expired(proposal), later]));
      await act(() => vi.advanceTimersByTimeAsync(20_500));
      await waitFor(() => expect(inQueue(proposal.question)).not.toBeInTheDocument());
      await waitFor(() => expect(api.calls(MARKETS)).toHaveLength(4));

      // The second proposal closes at 17:01: nothing is asked in between.
      api.on(MARKETS, lists([], [expired(proposal), expired(later)]));
      await act(() => vi.advanceTimersByTimeAsync(50_000));
      expect(api.calls(MARKETS)).toHaveLength(4);

      await act(() => vi.advanceTimersByTimeAsync(10_500));

      expect(await screen.findByText("No proposals waiting.")).toBeInTheDocument();
      await waitFor(() => expect(badgeInTable(later.question)).toBe("Expired"));
      expect(api.calls(MARKETS)).toHaveLength(6);
      // No proposal in review is left to wait for.
      await act(() => vi.advanceTimersByTimeAsync(120_000));
      expect(api.calls(MARKETS)).toHaveLength(6);
    });

    test("the timer decides nothing: a proposal the API still lists stays in the queue, and the lists are read again every live_poll_fallback_seconds", async () => {
      // The clock of the device runs ahead of the server's.
      const api = mockApi({ [MARKETS]: lists([proposal], [proposal]) });
      render(4);
      await within(await table("Proposals")).findByText(proposal.question);

      await act(() => vi.advanceTimersByTimeAsync(20_500));
      await waitFor(() => expect(queueReads(api)).toHaveLength(2));

      expect(inQueue(proposal.question)).toBeInTheDocument();
      expect(badgeInTable(proposal.question)).toBe("In review");
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();

      await act(() => vi.advanceTimersByTimeAsync(4_000));
      await waitFor(() => expect(queueReads(api)).toHaveLength(3));

      api.on(MARKETS, lists([], [expired(proposal)]));
      await act(() => vi.advanceTimersByTimeAsync(4_000));

      expect(await screen.findByText("No proposals waiting.")).toBeInTheDocument();
      await waitFor(() => expect(badgeInTable(proposal.question)).toBe("Expired"));
      const asked = api.calls(MARKETS).length;
      await act(() => vi.advanceTimersByTimeAsync(30_000));
      expect(api.calls(MARKETS)).toHaveLength(asked);
    });

    test("a proposal in review that only the queue shows is waited for: the table is on another status", async () => {
      const api = mockApi({ [MARKETS]: lists([proposal], [open]) });
      const { user } = render();
      await within(await table("Proposals")).findByText(proposal.question);
      await user.selectOptions(screen.getByLabelText("Status"), "Open");
      await waitFor(() => expect(api.calls(MARKETS).map(statuses)).toContainEqual(["OPEN"]));
      await within(await table("Markets")).findByText(open.question);
      const asked = api.calls(MARKETS).length;
      api.on(MARKETS, lists([], [open]));

      await act(() => vi.advanceTimersByTimeAsync(20_500));

      expect(await screen.findByText("No proposals waiting.")).toBeInTheDocument();
      // The table is read again with the queue, on the status it stands on.
      await waitFor(() => expect(api.calls(MARKETS)).toHaveLength(asked + 2));
      expect(api.calls(MARKETS).slice(asked).map(statuses)).toEqual(
        expect.arrayContaining([["PROPOSED"], ["OPEN"]]),
      );
    });

    test("a proposal in review that only the table shows is waited for too", async () => {
      // The queue could not be read; the table lists the proposal.
      const unavailable = apiError(
        503,
        "SERVICE_UNAVAILABLE",
        "This is temporarily unavailable. Try again shortly.",
      );
      const api = mockApi({
        [MARKETS]: (request) => (isQueue(request) ? unavailable : json(200, { items: [proposal] })),
      });
      render();
      await within(await table("Markets")).findByText(proposal.question);
      api.on(MARKETS, (request) =>
        isQueue(request) ? unavailable : json(200, { items: [expired(proposal)] }),
      );

      await act(() => vi.advanceTimersByTimeAsync(20_500));

      await waitFor(() => expect(badgeInTable(proposal.question)).toBe("Expired"));
      expect(tableReads(api)).toHaveLength(2);
    });

    test("a market that is not in review is not waited for", async () => {
      // Open for trading, paused and expired, each with the close time that is 20 s away.
      const soon = { close_at: proposal.close_at };
      const api = mockApi({
        [MARKETS]: lists(
          [],
          [
            marketSummary({ ...soon, id: "open" }),
            marketSummary({ ...soon, id: "paused", status: "HALTED", trading_open: false }),
            expired(marketSummary({ ...soon, id: "expired" })),
          ],
        ),
      });
      render();
      await screen.findByText("No proposals waiting.");
      await table("Markets");

      await act(() => vi.advanceTimersByTimeAsync(120_000));

      expect(api.calls(MARKETS)).toHaveLength(2);
    });

    test("is cleared when the screen goes away", async () => {
      const api = mockApi({ [MARKETS]: lists([proposal], [proposal]) });
      const { unmount } = render();
      await within(await table("Proposals")).findByText(proposal.question);
      await waitFor(() => expect(api.calls(MARKETS)).toHaveLength(2));

      unmount();
      await act(() => vi.advanceTimersByTimeAsync(120_000));

      expect(api.calls(MARKETS)).toHaveLength(2);
    });

    test("weeks before any close time nothing is asked", async () => {
      vi.setSystemTime(TODAY);
      const api = mockApi({ [MARKETS]: lists([proposal, later], [proposal, later, open]) });
      render();
      await within(await table("Proposals")).findByText(proposal.question);
      await waitFor(() => expect(api.calls(MARKETS)).toHaveLength(2));

      await act(() => vi.advanceTimersByTimeAsync(10 * 60_000));

      expect(api.calls(MARKETS)).toHaveLength(2);
    });
  });
});
