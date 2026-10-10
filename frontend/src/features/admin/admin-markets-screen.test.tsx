import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { marketSummary, MARKET_ID, me, OTHER_MARKET_ID } from "@/test/fixtures";
import { apiError, deferred, json, mockApi, type MockRequest, type Route } from "@/test/http";
import { navigation } from "@/test/navigation";
import { renderWithProviders } from "@/test/render";

import { AdminMarketsScreen } from "./admin-markets-screen";

const MARKETS: Route = "GET /api/markets";
const ALL_SEVEN = ["PROPOSED", "REJECTED", "OPEN", "HALTED", "CLOSED", "RESOLVED", "VOID"];

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

describe("Admin: markets", () => {
  test("stands under the three tabs, with a hidden h1 that names the screen", async () => {
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

  test("asks for the queue with PROPOSED, and for all markets with all seven statuses", async () => {
    const api = mockApi({ [MARKETS]: lists([proposal], [proposal, open]) });
    renderWithProviders(<AdminMarketsScreen />, admin);

    await within(await table("Markets")).findByText(open.question);
    const asked = api.calls(MARKETS).map(statuses);
    expect(asked).toHaveLength(2);
    expect(asked).toContainEqual(["PROPOSED"]);
    expect(asked).toContainEqual(ALL_SEVEN);
    // No request leaves the status out: that would hide proposals and rejected markets.
    expect(asked.every((sent) => sent.length > 0)).toBe(true);
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
});
