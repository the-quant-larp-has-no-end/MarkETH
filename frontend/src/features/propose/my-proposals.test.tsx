import { act, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { config, MARKET_ID, OTHER_MARKET_ID, proposal, TODAY } from "@/test/fixtures";
import { apiError, deferred, json, mockApi, type Route } from "@/test/http";
import { renderWithProviders } from "@/test/render";

import { MyProposals } from "./my-proposals";

const PROPOSALS: Route = "GET /api/me/proposals";

const rows = () => within(screen.getByRole("list")).getAllByRole("listitem");

// The list sets a timer for the earliest close time among its proposals in review: the
// tests stand on one day.
beforeEach(() => {
  vi.useFakeTimers({ now: TODAY, toFake: ["Date"] });
});

afterEach(() => {
  vi.useRealTimers();
});

describe("My proposals", () => {
  test("shows skeleton rows while the list loads", async () => {
    const answer = deferred();
    mockApi({ [PROPOSALS]: answer.handler });
    renderWithProviders(<MyProposals />);

    expect(screen.getByRole("heading", { level: 2, name: "My proposals" })).toBeInTheDocument();
    expect(document.querySelector("[data-slot=row-skeletons]")).toBeInTheDocument();
    expect(screen.queryByText("No proposals yet.")).not.toBeInTheDocument();

    answer.resolve(json(200, { items: [] }));
    await waitFor(() =>
      expect(document.querySelector("[data-slot=row-skeletons]")).not.toBeInTheDocument(),
    );
  });

  test("with none: the blackboard and one sentence", async () => {
    mockApi({ [PROPOSALS]: json(200, { items: [] }) });
    renderWithProviders(<MyProposals />);

    expect(await screen.findByText("No proposals yet.")).toBeInTheDocument();
    expect(document.querySelector("[data-slot=empty-state] svg")).toBeInTheDocument();
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
  });

  test("a failed read shows the API's message with Try again, which reads the list again", async () => {
    const api = mockApi({
      [PROPOSALS]: apiError(500, "INTERNAL_ERROR", "Something went wrong."),
    });
    const { user } = renderWithProviders(<MyProposals />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong.");

    api.on(PROPOSALS, json(200, { items: [proposal()] }));
    await user.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findByText(proposal().question)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  test("lists each proposal in the API's order with its question and its status badge", async () => {
    mockApi({
      [PROPOSALS]: json(200, {
        items: [
          proposal({ id: "a", question: "Will the Polybahn be free in 2027?" }),
          proposal({ id: "b", question: "Will the CAB get a second cafeteria?", status: "OPEN" }),
          proposal({ id: "c", question: "Will the HG dome be open at night?", status: "REJECTED" }),
          proposal({
            id: "d",
            question: "Will the Mensa serve fondue?",
            status: "RESOLVED",
            outcome: "YES",
          }),
          proposal({
            id: "e",
            question: "Will the ASVZ add a sauna?",
            status: "VOID",
            outcome: "VOID",
          }),
          proposal({ id: "f", question: "Will the Polyterrasse be rebuilt?", status: "HALTED" }),
          proposal({ id: "g", question: "Will the semester start late?", status: "CLOSED" }),
        ],
      }),
    });
    renderWithProviders(<MyProposals />);

    await screen.findByText("Will the Polybahn be free in 2027?");
    expect(rows().map((row) => row.textContent)).toEqual([
      "Will the Polybahn be free in 2027? In review",
      "Will the CAB get a second cafeteria? Open",
      "Will the HG dome be open at night? Not approved",
      "Will the Mensa serve fondue? Resolved YES",
      "Will the ASVZ add a sauna? Voided",
      "Will the Polyterrasse be rebuilt? Paused",
      "Will the semester start late? Closed",
    ]);
  });

  test("the badge of a resolved proposal names its outcome, from `outcome` of the row, in the outcome's colour", async () => {
    mockApi({
      [PROPOSALS]: json(200, {
        items: [
          proposal({ id: "y", question: "Resolved to YES?", status: "RESOLVED", outcome: "YES" }),
          proposal({ id: "n", question: "Resolved to NO?", status: "RESOLVED", outcome: "NO" }),
          proposal({ id: "p", question: "Still in review?", status: "PROPOSED", outcome: null }),
        ],
      }),
    });
    renderWithProviders(<MyProposals />);

    await screen.findByText("Resolved to YES?");
    const badges = rows().map((row) => row.querySelector<HTMLElement>("[data-slot=badge]")!);
    expect(badges.map((badge) => badge.textContent)).toEqual([
      "Resolved YES",
      "Resolved NO",
      "In review",
    ]);
    expect(badges[0]).toHaveAttribute("data-variant", "yes");
    expect(badges[1]).toHaveAttribute("data-variant", "no");
    // Green and red are for an outcome alone.
    expect(badges[2]).toHaveAttribute("data-variant", "raised");
  });

  test("shows nothing else about the market: no price, no figure, no time", async () => {
    mockApi({
      [PROPOSALS]: json(200, {
        items: [proposal({ id: "a", question: "Only the question?", status: "OPEN" })],
      }),
    });
    renderWithProviders(<MyProposals />);

    await screen.findByText("Only the question?");
    const row = rows()[0]!;
    expect(row.textContent).toBe("Only the question? Open");
    expect(row.querySelector("[data-slot=amount]")).toBeNull();
    expect(row.querySelector("time")).toBeNull();
  });

  test("a rejected proposal shows the reason VIS gave; no other proposal shows one", async () => {
    mockApi({
      [PROPOSALS]: json(200, {
        items: [
          proposal({
            id: "a",
            status: "REJECTED",
            rejection_reason: "One trader can decide this outcome alone.",
          }),
          // A reason that an API should not send for this status is not shown.
          proposal({ id: "b", status: "PROPOSED", rejection_reason: "Left over." }),
        ],
      }),
    });
    renderWithProviders(<MyProposals />);

    expect(
      await screen.findByText("One trader can decide this outcome alone."),
    ).toBeInTheDocument();
    expect(screen.queryByText("Left over.")).not.toBeInTheDocument();
  });

  test("a proposal that was not reviewed before its closing time is Expired, and says so in one line", async () => {
    mockApi({
      [PROPOSALS]: json(200, {
        items: [
          proposal({
            id: "e",
            question: "Will the Polyterrasse get deckchairs this term?",
            status: "EXPIRED",
            close_at: "2026-10-01T16:00:00Z",
          }),
          proposal({ id: "p", question: "A proposal in review?", status: "PROPOSED" }),
        ],
      }),
    });
    renderWithProviders(<MyProposals />);

    const expired = (
      await screen.findByText("Will the Polyterrasse get deckchairs this term?")
    ).closest("li")!;
    const badge = within(expired).getByText("Expired");
    expect(badge).toHaveAttribute("data-slot", "badge");
    // The look of "Closed" and "Not approved": neither green nor red.
    expect(badge).toHaveAttribute("data-variant", "muted");
    expect(badge).toHaveClass("bg-raised", "text-muted-foreground", "shrink-0");
    const line = within(expired).getByText("It was not reviewed before its closing time.");
    expect(line).toHaveClass("type-body-sm", "text-muted-foreground");
    // It is in review no more, and nothing else is said about it.
    expect(within(expired).queryByText("In review")).not.toBeInTheDocument();
    expect(expired.querySelectorAll("[data-slot=badge]")).toHaveLength(1);
    expect(expired.textContent).toBe(
      "Will the Polyterrasse get deckchairs this term? It was not reviewed before its closing time. Expired",
    );
    expect(expired.querySelector("time")).toBeNull();

    // The proposal that is still in review says so, and has no such line.
    const waiting = screen.getByText("A proposal in review?").closest("li")!;
    expect(within(waiting).getByText("In review")).toBeInTheDocument();
    expect(waiting).not.toHaveTextContent("It was not reviewed");
  });

  test("an expired proposal shows no reason of a rejection, and is no link: it never became a market", async () => {
    mockApi({
      [PROPOSALS]: json(200, {
        items: [
          proposal({
            id: MARKET_ID,
            question: "An expired proposal?",
            status: "EXPIRED",
            rejection_reason: "Left over.",
          }),
        ],
      }),
    });
    renderWithProviders(<MyProposals />);

    const row = (await screen.findByText("An expired proposal?")).closest("li")!;
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(row.querySelector("a")).toBeNull();
    expect(row).not.toHaveTextContent("Left over.");
    expect(row).toHaveTextContent("It was not reviewed before its closing time.");
  });

  test("a proposal that became a market is a link to it; one in review or not approved is not", async () => {
    mockApi({
      [PROPOSALS]: json(200, {
        items: [
          proposal({ id: MARKET_ID, question: "An open market?", status: "OPEN" }),
          proposal({ id: OTHER_MARKET_ID, question: "A settled market?", status: "RESOLVED" }),
          proposal({ id: "p", question: "A proposal in review?", status: "PROPOSED" }),
          proposal({ id: "r", question: "A rejected proposal?", status: "REJECTED" }),
        ],
      }),
    });
    renderWithProviders(<MyProposals />);

    await screen.findByText("An open market?");
    const links = screen.getAllByRole("link");
    expect(links.map((link) => link.getAttribute("href"))).toEqual([
      `/markets/${MARKET_ID}`,
      `/markets/${OTHER_MARKET_ID}`,
    ]);
    expect(links[0]).toHaveTextContent("An open market?");
    expect(screen.getByText("A proposal in review?").closest("a")).toBeNull();
    expect(screen.getByText("A rejected proposal?").closest("a")).toBeNull();
  });

  test("a row is at least 56 px tall, a tap target of its own", async () => {
    mockApi({ [PROPOSALS]: json(200, { items: [proposal({ status: "OPEN" })] }) });
    renderWithProviders(<MyProposals />);

    expect(await screen.findByRole("link")).toHaveClass("min-h-14");
  });

  describe("when a proposal in review reaches its closing time", () => {
    const ASVZ = proposal().question;
    const CAB = "Will the CAB get a second cafeteria?";
    /** Twenty seconds before the ASVZ proposal closes, at 17:00 UTC. */
    const BEFORE_CLOSE = new Date("2026-12-11T16:59:40Z");

    const asvz = () => proposal();
    /** A second proposal in review, which closes a minute after the first. */
    const cab = () => proposal({ id: "cab", question: CAB, close_at: "2026-12-11T17:01:00Z" });
    const expired = (row: ReturnType<typeof proposal>) => proposal({ ...row, status: "EXPIRED" });

    const badgeOf = (question: string) =>
      screen.getByText(question).closest("li")!.querySelector("[data-slot=badge]")!.textContent;

    const render = (pollSeconds = 10) =>
      renderWithProviders(<MyProposals />, {
        config: config({ live_poll_fallback_seconds: pollSeconds }),
      });

    beforeEach(() => {
      vi.useFakeTimers({ now: BEFORE_CLOSE, shouldAdvanceTime: true });
    });

    test("reads the list again at the earliest closing time among the proposals in review, and the row says what the answer says", async () => {
      // The later one stands first: the list is newest first, not soonest first.
      const api = mockApi({ [PROPOSALS]: json(200, { items: [cab(), asvz()] }) });
      render();
      await screen.findByText(ASVZ);
      expect(badgeOf(ASVZ)).toBe("In review");
      api.on(PROPOSALS, json(200, { items: [cab(), expired(asvz())] }));

      // Not before its time.
      await act(() => vi.advanceTimersByTimeAsync(15_000));
      expect(api.calls(PROPOSALS)).toHaveLength(1);

      await act(() => vi.advanceTimersByTimeAsync(5_500));

      await waitFor(() => expect(api.calls(PROPOSALS)).toHaveLength(2));
      await waitFor(() => expect(badgeOf(ASVZ)).toBe("Expired"));
      expect(screen.getByText(ASVZ).closest("li")).toHaveTextContent(
        "It was not reviewed before its closing time.",
      );
      // The other proposal is in review as before.
      expect(badgeOf(CAB)).toBe("In review");
    });

    test("after the answer it waits for the earliest closing time that is left", async () => {
      const api = mockApi({ [PROPOSALS]: json(200, { items: [asvz(), cab()] }) });
      render();
      await screen.findByText(ASVZ);
      api.on(PROPOSALS, json(200, { items: [expired(asvz()), cab()] }));
      await act(() => vi.advanceTimersByTimeAsync(20_500));
      await waitFor(() => expect(api.calls(PROPOSALS)).toHaveLength(2));
      await waitFor(() => expect(badgeOf(ASVZ)).toBe("Expired"));

      // The second proposal closes at 17:01: nothing is asked in between.
      api.on(PROPOSALS, json(200, { items: [expired(asvz()), expired(cab())] }));
      await act(() => vi.advanceTimersByTimeAsync(50_000));
      expect(api.calls(PROPOSALS)).toHaveLength(2);

      await act(() => vi.advanceTimersByTimeAsync(10_500));

      await waitFor(() => expect(api.calls(PROPOSALS)).toHaveLength(3));
      await waitFor(() => expect(badgeOf(CAB)).toBe("Expired"));
      // No proposal in review is left to wait for.
      await act(() => vi.advanceTimersByTimeAsync(120_000));
      expect(api.calls(PROPOSALS)).toHaveLength(3);
    });

    test("the timer decides nothing: a proposal the API still calls PROPOSED stays In review, and the list is read again every live_poll_fallback_seconds", async () => {
      // The clock of the device runs ahead of the server's.
      const api = mockApi({ [PROPOSALS]: json(200, { items: [asvz()] }) });
      render(4);
      await screen.findByText(ASVZ);

      await act(() => vi.advanceTimersByTimeAsync(20_500));
      await waitFor(() => expect(api.calls(PROPOSALS)).toHaveLength(2));

      expect(badgeOf(ASVZ)).toBe("In review");
      expect(screen.queryByText("It was not reviewed before its closing time.")).toBeNull();
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();

      await act(() => vi.advanceTimersByTimeAsync(4_000));
      await waitFor(() => expect(api.calls(PROPOSALS)).toHaveLength(3));

      api.on(PROPOSALS, json(200, { items: [expired(asvz())] }));
      await act(() => vi.advanceTimersByTimeAsync(4_000));

      await waitFor(() => expect(badgeOf(ASVZ)).toBe("Expired"));
      const asked = api.calls(PROPOSALS).length;
      await act(() => vi.advanceTimersByTimeAsync(30_000));
      expect(api.calls(PROPOSALS)).toHaveLength(asked);
    });

    test("only a proposal in review is waited for", async () => {
      // Every other status, with the same closing time. The timer is for what leaves
      // review; a market that is open for trading is followed where it is traded, on
      // Markets and on Market detail.
      const api = mockApi({
        [PROPOSALS]: json(200, {
          items: (
            ["REJECTED", "EXPIRED", "OPEN", "HALTED", "CLOSED", "RESOLVED", "VOID"] as const
          ).map((status) => proposal({ id: status, question: `${status}?`, status })),
        }),
      });
      render();
      await screen.findByText("OPEN?");

      await act(() => vi.advanceTimersByTimeAsync(120_000));

      expect(api.calls(PROPOSALS)).toHaveLength(1);
    });

    test("is cleared when the screen goes away", async () => {
      const api = mockApi({ [PROPOSALS]: json(200, { items: [asvz()] }) });
      const { unmount } = render();
      await screen.findByText(ASVZ);

      unmount();
      await act(() => vi.advanceTimersByTimeAsync(120_000));

      expect(api.calls(PROPOSALS)).toHaveLength(1);
    });

    test("weeks before any closing time nothing is asked", async () => {
      vi.setSystemTime(TODAY);
      const api = mockApi({ [PROPOSALS]: json(200, { items: [asvz(), cab()] }) });
      render();
      await screen.findByText(ASVZ);

      await act(() => vi.advanceTimersByTimeAsync(10 * 60_000));

      expect(api.calls(PROPOSALS)).toHaveLength(1);
    });
  });
});
