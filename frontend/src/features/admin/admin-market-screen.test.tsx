import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import type { MarketDetailResponse, MarketStatus } from "@/lib/api/schema";
import { adminMarketDetail, MARKET_ID, marketViewer, me } from "@/test/fixtures";
import { apiError, deferred, json, mockApi, type Route } from "@/test/http";
import { renderWithProviders } from "@/test/render";

import { AdminMarketScreen } from "./admin-market-screen";

const MARKET: Route = "GET /api/markets/{market_id}";
const TRADED = "You traded this market, so another admin has to settle it.";

const admin = { me: me({ role: "ADMIN" }) };

/** A market as an admin who did not trade it reads it. */
const adminMarket = adminMarketDetail;

function show(market: MarketDetailResponse) {
  mockApi({ [MARKET]: json(200, market) });
  return renderWithProviders(<AdminMarketScreen marketId={market.id} />, admin);
}

/** The value that stands beside a label of the rules or the details. */
function stated(term: string): string | null {
  const label = screen.getAllByText(term).find((element) => element.tagName === "DT");
  return label?.parentElement?.querySelector("dd")?.textContent ?? null;
}

const actions = () =>
  within(screen.getByRole("region", { name: "Actions" }))
    .getAllByRole("button")
    .map((button) => button.textContent);

describe("Admin: one market", () => {
  test("is an inner screen whose h1 is the question, with the status badge", async () => {
    const api = mockApi({ [MARKET]: json(200, adminMarket()) });
    renderWithProviders(<AdminMarketScreen marketId={MARKET_ID} />, admin);

    expect(
      await screen.findByRole("heading", {
        level: 1,
        name: "Will the Polybahn run without interruption during the exam session?",
      }),
    ).toBeInTheDocument();
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Back" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Open");
    expect(api.lastCall(MARKET)?.path).toBe(`/api/markets/${MARKET_ID}`);
  });

  test("shows skeletons in the shape of the screen while the market loads, under a hidden h1", async () => {
    const answer = deferred();
    mockApi({ [MARKET]: answer.handler });
    renderWithProviders(<AdminMarketScreen marketId={MARKET_ID} />, admin);

    expect(document.querySelector("[data-slot=market-skeleton]")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Market");
    expect(screen.getByRole("heading", { level: 1 })).toHaveClass("sr-only");

    answer.resolve(json(200, adminMarket()));
    await waitFor(() =>
      expect(document.querySelector("[data-slot=market-skeleton]")).not.toBeInTheDocument(),
    );
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  });

  test("states the rules as Market detail does, with the times in the timezone of /config", async () => {
    show(adminMarket());
    await screen.findByRole("heading", { level: 2, name: "Rules" });

    expect(stated("YES means")).toBe("No interruption of more than 30 minutes is reported.");
    expect(stated("NO means")).toBe(
      "At least one interruption of more than 30 minutes is reported.",
    );
    expect(stated("How it is decided")).toBe(
      "Decided from the operator's published service notices for the session.",
    );
    expect(stated("Source")).toBe("polybahn.ch service notices");
    expect(stated("Trading closes")).toBe("14 Oct 2026, 18:00");
    expect(stated("Decided by")).toBe("21 Oct 2026, 18:00");
  });

  test("shows the facts only an admin needs, as the API sent them", async () => {
    show(
      adminMarket({
        tier: "DEFAULT",
        b_cr: "1000",
        max_loss_cr: "693.147181",
        created_at: "2026-10-01T09:00:00Z",
        opened_at: "2026-10-01T12:00:00Z",
        closed_at: null,
      }),
    );
    await screen.findByRole("heading", { level: 2, name: "Details" });

    expect(stated("Proposed by")).toBe("bea.beispiel@ethz.ch");
    // A proposal asks for no tier: there is the one VIS gave the market, and no other.
    expect(screen.queryByText(/requested/i)).not.toBeInTheDocument();
    expect(stated("Tier")).toBe("Default");
    expect(stated("Liquidity b")).toBe("1,0001,000 tokens");
    // Cut after the second decimal, never rounded up.
    expect(stated("Loss budget")).toBe("693.14693.14 tokens");
    expect(stated("Proposed")).toBe("1 Oct 2026, 11:00");
    expect(stated("Opened")).toBe("1 Oct 2026, 14:00");
    expect(stated("Closed")).toBe("–none yet");
    expect(screen.queryByText("Rejection reason")).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Resolution" })).not.toBeInTheDocument();
    // The facts stand in the order of DESIGN.md §13: who and when first, the pricing last.
    const details = screen.getByRole("heading", { level: 2, name: "Details" }).parentElement!;
    expect([...details.querySelectorAll("dt")].map((term) => term.textContent)).toEqual([
      "Proposed by",
      "Proposed",
      "Opened",
      "Closed",
      "Tier",
      "Liquidity b",
      "Loss budget",
    ]);
  });

  test("a proposal has no tier yet, and a custom tier is named Custom", async () => {
    const { unmount } = show(
      adminMarket({
        status: "PROPOSED",
        trading_open: false,
        tier: null,
        b_cr: "0",
        max_loss_cr: "0",
        opened_at: null,
      }),
    );
    await screen.findByRole("heading", { level: 2, name: "Details" });
    expect(stated("Tier")).toBe("–none yet");
    // Liquidity b and loss budget come with the tier: until the approval each is a dash,
    // also though the API sends a zero for the two.
    expect(stated("Liquidity b")).toBe("–none yet");
    expect(stated("Loss budget")).toBe("–none yet");
    expect(stated("Opened")).toBe("–none yet");
    unmount();

    show(adminMarket({ id: "custom", tier: "CUSTOM", b_cr: "3606.737602", max_loss_cr: "2500" }));
    await screen.findByRole("heading", { level: 2, name: "Details" });
    expect(stated("Tier")).toBe("Custom");
    expect(stated("Liquidity b")).toBe("3,606.733,606.73 tokens");
  });

  test("a rejected proposal shows why", async () => {
    show(
      adminMarket({
        status: "REJECTED",
        trading_open: false,
        tier: null,
        rejection_reason: "One trader can decide this outcome alone.",
      }),
    );

    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Not approved"));
    expect(stated("Rejection reason")).toBe("One trader can decide this outcome alone.");
  });

  test("a proposal whose close time passed shows the badge Expired and offers neither Approve nor Reject", async () => {
    const api = mockApi({
      [MARKET]: json(
        200,
        adminMarket({
          status: "EXPIRED",
          trading_open: false,
          tier: null,
          b_cr: "0",
          max_loss_cr: "0",
          void_cost_cr: null,
          opened_at: null,
        }),
      ),
    });
    renderWithProviders(<AdminMarketScreen marketId={MARKET_ID} />, admin);

    await screen.findByRole("heading", { level: 2, name: "Rules" });
    const badge = within(screen.getByRole("status")).getByText("Expired");
    expect(badge).toHaveAttribute("data-slot", "badge");
    expect(screen.queryByText("In review")).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Actions" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Approve|Reject/ })).not.toBeInTheDocument();
    // What the screen shows is the status the API sent: nothing here looks at a clock.
    expect(api.calls(MARKET)).toHaveLength(1);
    expect(stated("Tier")).toBe("–none yet");
  });

  test.each([
    ["YES", "RESOLVED", "Resolved YES"],
    ["NO", "RESOLVED", "Resolved NO"],
    ["VOID", "VOID", "Voided"],
  ] as const)("the resolution of a market that ended %s", async (outcome, status, badge) => {
    show(
      adminMarket({
        status,
        trading_open: false,
        outcome,
        closed_at: "2026-10-14T16:00:00Z",
        resolution: {
          outcome,
          evidence: "The operator's notices list no interruption.",
          resolved_at: "2026-10-15T08:30:00Z",
        },
      }),
    );

    const heading = await screen.findByRole("heading", { level: 2, name: "Resolution" });
    const resolution = within(heading.parentElement!);
    expect(resolution.getByText(badge)).toBeInTheDocument();
    expect(
      resolution.getByText("The operator's notices list no interruption."),
    ).toBeInTheDocument();
    expect(resolution.getByText("15 Oct 2026, 10:30")).toBeInTheDocument();
    expect(stated("Closed")).toBe("14 Oct 2026, 18:00");
  });

  test("a market the API does not know is the not-found screen", async () => {
    mockApi({ [MARKET]: apiError(404, "NOT_FOUND", "Not found.") });
    renderWithProviders(<AdminMarketScreen marketId={MARKET_ID} />, admin);

    expect(
      await screen.findByText("This page does not exist. Not even in the HG basement."),
    ).toBeInTheDocument();
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.queryByText("Not found.")).not.toBeInTheDocument();
  });

  test("an id that cannot be one is the not-found screen too", async () => {
    mockApi({
      [MARKET]: apiError(422, "VALIDATION_ERROR", "The request is not valid.", {
        errors: [{ loc: ["path", "market_id"], msg: "Input should be a valid UUID", type: "uuid" }],
      }),
    });
    renderWithProviders(<AdminMarketScreen marketId="not-an-id" />, admin);

    expect(
      await screen.findByText("This page does not exist. Not even in the HG basement."),
    ).toBeInTheDocument();
  });

  test("a market that cannot be read shows the API's message with Try again", async () => {
    const api = mockApi({ [MARKET]: apiError(500, "INTERNAL_ERROR", "Something went wrong.") });
    const { user } = renderWithProviders(<AdminMarketScreen marketId={MARKET_ID} />, admin);

    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong.");
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);

    api.on(MARKET, json(200, adminMarket()));
    await user.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findByRole("heading", { level: 2, name: "Rules" })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("the action bar offers only what the status allows", () => {
  test.each([
    ["PROPOSED", ["Approve", "Reject"]],
    ["OPEN", ["Halt", "Close"]],
    ["HALTED", ["Resume", "Close"]],
    ["CLOSED", ["Resolve", "Void"]],
  ] as const)("%s: %j", async (status, expected) => {
    show(adminMarket({ status, trading_open: status === "OPEN" }));
    await screen.findByRole("heading", { level: 2, name: "Rules" });

    expect(actions()).toEqual(expected);
    // At most one white button on the screen.
    expect(
      document.querySelectorAll("[data-slot=button][data-variant=primary]").length,
    ).toBeLessThanOrEqual(1);
    for (const button of within(screen.getByRole("region", { name: "Actions" })).getAllByRole(
      "button",
    )) {
      expect(button).toBeEnabled();
      // 48 px tall.
      expect(button).toHaveClass("h-12");
    }
  });

  test.each(["EXPIRED", "REJECTED", "RESOLVED", "VOID"] as const satisfies readonly MarketStatus[])(
    "%s: nothing is left to do",
    async (status) => {
      show(adminMarket({ status, trading_open: false }));
      await screen.findByRole("heading", { level: 2, name: "Rules" });

      expect(screen.queryByRole("region", { name: "Actions" })).not.toBeInTheDocument();
      expect(
        screen.queryByRole("button", {
          name: /Approve|Reject|Halt|Resume|Close|Resolve|Void/,
        }),
      ).not.toBeInTheDocument();
    },
  );

  test("an admin who traded the market finds Resolve and Void disabled, and the API's sentence above them", async () => {
    show(
      adminMarket({
        status: "CLOSED",
        trading_open: false,
        viewer: marketViewer({ can_settle: false }),
      }),
    );
    await screen.findByRole("heading", { level: 2, name: "Rules" });

    const bar = screen.getByRole("region", { name: "Actions" });
    expect(within(bar).getByRole("button", { name: "Resolve" })).toBeDisabled();
    expect(within(bar).getByRole("button", { name: "Void" })).toBeDisabled();
    const banner = within(bar).getByText(TRADED);
    expect(banner.closest("[data-slot=alert]")).toHaveAttribute("data-variant", "neutral");
    expect(
      banner.compareDocumentPosition(within(bar).getByRole("button", { name: "Resolve" })) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).not.toBe(0);
  });

  test("who may settle says nothing about the other actions", async () => {
    show(adminMarket({ status: "OPEN", viewer: marketViewer({ can_settle: false }) }));
    await screen.findByRole("heading", { level: 2, name: "Rules" });

    expect(screen.queryByText(TRADED)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Halt" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Close" })).toBeEnabled();
  });

  test("an admin who may settle sees no banner", async () => {
    show(adminMarket({ status: "CLOSED", trading_open: false }));
    await screen.findByRole("heading", { level: 2, name: "Rules" });

    expect(screen.queryByText(TRADED)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Resolve" })).toBeEnabled();
  });
});
