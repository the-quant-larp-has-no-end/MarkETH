import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { MARKET_ID, OTHER_MARKET_ID, proposal } from "@/test/fixtures";
import { apiError, deferred, json, mockApi, type Route } from "@/test/http";
import { renderWithProviders } from "@/test/render";

import { MyProposals } from "./my-proposals";

const PROPOSALS: Route = "GET /api/me/proposals";

const rows = () => within(screen.getByRole("list")).getAllByRole("listitem");

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
});
