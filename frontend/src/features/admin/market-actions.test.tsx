import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";

import { keys } from "@/lib/api/keys";
import type { MarketDetailResponse } from "@/lib/api/schema";
import {
  adminMarketDetail,
  MARKET_ID,
  marketViewer,
  me,
  risk,
  riskMarketRow,
} from "@/test/fixtures";
import { apiError, deferred, json, mockApi, noAnswer, type Route } from "@/test/http";
import { createTestQueryClient, renderWithProviders } from "@/test/render";

import { AdminMarketScreen } from "./admin-market-screen";

const MARKET: Route = "GET /api/markets/{market_id}";
const ME: Route = "GET /api/me";
const RISK: Route = "GET /api/admin/risk";
const APPROVE: Route = "POST /api/admin/markets/{market_id}/approve";
const REJECT: Route = "POST /api/admin/markets/{market_id}/reject";
const HALT: Route = "POST /api/admin/markets/{market_id}/halt";
const RESUME: Route = "POST /api/admin/markets/{market_id}/resume";
const CLOSE: Route = "POST /api/admin/markets/{market_id}/close";
const RESOLVE: Route = "POST /api/admin/markets/{market_id}/resolve";
const VOID: Route = "POST /api/admin/markets/{market_id}/void";

const TRADED = "You traded this market, so another admin has to settle it.";
const NOT_POSSIBLE = "This is not possible in the current state.";

const adminUser = me({ role: "ADMIN" });

/** A market as an admin who did not trade it reads it. */
const adminMarket = adminMarketDetail;

const proposed = (overrides: Partial<MarketDetailResponse> = {}) =>
  adminMarket({
    status: "PROPOSED",
    trading_open: false,
    tier: null,
    b_cr: "0",
    max_loss_cr: "0",
    opened_at: null,
    ...overrides,
  });
const halted = () => adminMarket({ status: "HALTED", trading_open: false });
const closed = (overrides: Partial<MarketDetailResponse> = {}) =>
  adminMarket({
    status: "CLOSED",
    trading_open: false,
    closed_at: "2026-10-14T16:00:00Z",
    ...overrides,
  });

function show(market: MarketDetailResponse, routes: Partial<Record<Route, Response>> = {}) {
  const api = mockApi({ [MARKET]: json(200, market), [ME]: json(200, adminUser), ...routes });
  const rendered = renderWithProviders(<AdminMarketScreen marketId={MARKET_ID} />, {
    me: adminUser,
  });
  return { api, ...rendered };
}

type User = ReturnType<typeof renderWithProviders>["user"];

async function openDialog(
  user: User,
  action: string,
  role: "dialog" | "alertdialog",
  name: string,
) {
  await user.click(await screen.findByRole("button", { name: action }));
  return screen.findByRole(role, { name });
}

/** Opens the approval of a proposal and picks the tier the admin gives it. */
async function openApproval(user: User, tier = "Medium") {
  const dialog = await openDialog(user, "Approve", "dialog", "Approve market");
  await user.click(within(dialog).getByRole("radio", { name: tier }));
  return dialog;
}

const gone = (role: "dialog" | "alertdialog") =>
  waitFor(() => expect(screen.queryByRole(role)).not.toBeInTheDocument());

const status = () => screen.getByRole("status");
const bar = () =>
  within(screen.getByRole("region", { name: "Actions" }))
    .getAllByRole("button")
    .map((button) => button.textContent);

describe("Approve", () => {
  test("offers the tiers of /config with their figures as sent, and Custom; the admin picks one, and none is chosen to begin with", async () => {
    const { user } = show(proposed());
    const dialog = await openDialog(user, "Approve", "dialog", "Approve market");

    const group = within(dialog).getByRole("radiogroup", { name: "Liquidity" });
    expect(
      within(group)
        .getAllByRole("radio")
        .map((radio) => radio.getAttribute("value")),
    ).toEqual(["TIGHT", "DEFAULT", "MEDIUM", "CUSTOM"]);
    expect(within(group).queryByRole("radio", { checked: true })).not.toBeInTheDocument();
    // Nothing can be approved before a tier is picked.
    expect(within(dialog).getByRole("button", { name: "Approve" })).toBeDisabled();
    expect(within(group).getByRole("radio", { name: "Tight" })).toHaveAccessibleDescription(
      "Liquidity b 500 tokens Loss budget 346.57 tokens",
    );
    expect(within(group).getByRole("radio", { name: "Default" })).toHaveAccessibleDescription(
      "Liquidity b 1,000 tokens Loss budget 693.14 tokens",
    );
    expect(within(group).getByRole("radio", { name: "Custom" })).not.toBeChecked();
    // No budget is asked for until Custom is chosen.
    expect(within(dialog).queryByLabelText("Loss budget")).not.toBeInTheDocument();
  });

  test("sends the tier alone when nothing was changed, and the screen updates from the answer", async () => {
    const opened = adminMarket({ status: "OPEN", tier: "MEDIUM", b_cr: "2000" });
    const { api, user } = show(proposed(), { [APPROVE]: json(200, opened) });
    const dialog = await openApproval(user);

    await user.click(within(dialog).getByRole("button", { name: "Approve" }));

    await gone("dialog");
    expect(api.lastCall(APPROVE)?.path).toBe(`/api/admin/markets/${MARKET_ID}/approve`);
    expect(api.lastCall(APPROVE)?.body).toEqual({ tier: "MEDIUM" });
    expect(status()).toHaveTextContent("Open");
    expect(bar()).toEqual(["Halt", "Close"]);
    // The answer is the market: it was not asked for a second time.
    expect(api.calls(MARKET)).toHaveLength(1);
  });

  test("Custom asks for a budget within the most /config allows, and sends it as it was typed", async () => {
    const { api, user } = show(proposed(), {
      [APPROVE]: json(200, adminMarket({ tier: "CUSTOM" })),
    });
    const dialog = await openDialog(user, "Approve", "dialog", "Approve market");

    await user.click(within(dialog).getByRole("radio", { name: "Custom" }));
    const budget = within(dialog).getByLabelText("Loss budget");
    expect(budget).toHaveAccessibleDescription("At most 5,000 tokens.");
    expect(within(dialog).getByRole("button", { name: "Approve" })).toBeDisabled();

    await user.type(budget, " 1000.000001 ");
    await user.click(within(dialog).getByRole("button", { name: "Approve" }));

    await gone("dialog");
    // The string that was typed, not a number made from it.
    expect(api.lastCall(APPROVE)?.rawBody).toBe('{"tier":"CUSTOM","budget_cr":"1000.000001"}');
  });

  test("a fixed tier sends no budget, even when one was typed before", async () => {
    const { api, user } = show(proposed(), { [APPROVE]: json(200, adminMarket()) });
    const dialog = await openDialog(user, "Approve", "dialog", "Approve market");

    await user.click(within(dialog).getByRole("radio", { name: "Custom" }));
    await user.type(within(dialog).getByLabelText("Loss budget"), "750");
    await user.click(within(dialog).getByRole("radio", { name: "Tight" }));
    await user.click(within(dialog).getByRole("button", { name: "Approve" }));

    await gone("dialog");
    expect(api.lastCall(APPROVE)?.body).toEqual({ tier: "TIGHT" });
  });

  test("the wording and the two times can be changed, and only what was changed is sent", async () => {
    const { api, user } = show(proposed(), { [APPROVE]: json(200, adminMarket()) });
    const dialog = await openApproval(user);

    const edit = within(dialog).getByRole("button", { name: "Edit wording and times" });
    expect(edit).toHaveAttribute("aria-expanded", "false");
    expect(within(dialog).queryByLabelText("Question")).not.toBeInTheDocument();
    await user.click(edit);
    expect(edit).toHaveAttribute("aria-expanded", "true");

    // The fields hold what the proposal says, the times in the timezone of /config.
    const question = within(dialog).getByLabelText("Question");
    expect(question).toHaveValue(
      "Will the Polybahn run without interruption during the exam session?",
    );
    expect(within(dialog).getByLabelText("YES means")).toHaveValue(
      "No interruption of more than 30 minutes is reported.",
    );
    expect(within(dialog).getByLabelText("Trading closes")).toHaveValue("2026-10-14T18:00");
    expect(within(dialog).getByLabelText("Decided by")).toHaveValue("2026-10-21T18:00");

    await user.clear(question);
    await user.click(question);
    await user.paste("Will the Polybahn run without a break during the exam session?");
    await user.clear(within(dialog).getByLabelText("Trading closes"));
    await user.type(within(dialog).getByLabelText("Trading closes"), "2026-10-15T12:00");
    await user.click(within(dialog).getByRole("button", { name: "Approve" }));

    await gone("dialog");
    expect(api.lastCall(APPROVE)?.body).toEqual({
      tier: "MEDIUM",
      question: "Will the Polybahn run without a break during the exam session?",
      close_at: "2026-10-15T12:00:00+02:00",
    });
  });

  test("a time that was entered again as it was is no change", async () => {
    const { api, user } = show(proposed(), { [APPROVE]: json(200, adminMarket()) });
    const dialog = await openApproval(user);
    await user.click(within(dialog).getByRole("button", { name: "Edit wording and times" }));

    await user.clear(within(dialog).getByLabelText("Decided by"));
    await user.type(within(dialog).getByLabelText("Decided by"), "2026-10-21T18:00");
    await user.click(within(dialog).getByRole("button", { name: "Approve" }));

    await gone("dialog");
    expect(api.lastCall(APPROVE)?.body).toEqual({ tier: "MEDIUM" });
  });

  test("folding the fields away drops what was changed in them, and an emptied field cannot be sent", async () => {
    const { api, user } = show(proposed(), { [APPROVE]: json(200, adminMarket()) });
    const dialog = await openApproval(user);
    const edit = within(dialog).getByRole("button", { name: "Edit wording and times" });
    const approve = within(dialog).getByRole("button", { name: "Approve" });

    await user.click(edit);
    await user.clear(within(dialog).getByLabelText("Source"));
    expect(approve).toBeDisabled();

    await user.click(edit);
    expect(within(dialog).queryByLabelText("Source")).not.toBeInTheDocument();
    expect(approve).toBeEnabled();
    await user.click(approve);

    await gone("dialog");
    expect(api.lastCall(APPROVE)?.body).toEqual({ tier: "MEDIUM" });
  });

  test("LIMIT_EXCEEDED shows the API's message in the dialog, and the market is read again", async () => {
    const { api, user } = show(proposed(), {
      [APPROVE]: apiError(
        409,
        "LIMIT_EXCEEDED",
        "The limit of open markets is reached. Close one before you open another.",
        { limit: "MAX_OPEN_MARKETS" },
      ),
    });
    const dialog = await openApproval(user);

    await user.click(within(dialog).getByRole("button", { name: "Approve" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "The limit of open markets is reached. Close one before you open another.",
    );
    expect(screen.getByRole("dialog", { name: "Approve market" })).toBeInTheDocument();
    await waitFor(() => expect(api.calls(MARKET)).toHaveLength(2));
    expect(within(dialog).getByRole("button", { name: "Approve" })).toBeEnabled();
  });

  test("a budget above the most a market may have is said on the budget field", async () => {
    const { user } = show(proposed(), {
      [APPROVE]: apiError(
        409,
        "LIMIT_EXCEEDED",
        "This budget is above the most a market may have.",
        {
          limit: "MAX_MARKET_BUDGET",
        },
      ),
    });
    const dialog = await openDialog(user, "Approve", "dialog", "Approve market");

    await user.click(within(dialog).getByRole("radio", { name: "Custom" }));
    await user.type(within(dialog).getByLabelText("Loss budget"), "999999");
    await user.click(within(dialog).getByRole("button", { name: "Approve" }));

    await waitFor(() => expect(within(dialog).getByLabelText("Loss budget")).toBeInvalid());
    expect(within(dialog).getByLabelText("Loss budget")).toHaveAccessibleDescription(
      /This budget is above the most a market may have\./,
    );
    expect(within(dialog).getAllByRole("alert")).toHaveLength(1);
    // What was typed stays as it was typed.
    expect(within(dialog).getByLabelText("Loss budget")).toHaveValue("999999");
  });

  test("an error on a time shows that field, although the fields were folded away", async () => {
    const { user } = show(proposed(), {
      [APPROVE]: apiError(422, "VALIDATION_ERROR", "Trading has to close in the future.", {
        field: "close_at",
      }),
    });
    const dialog = await openApproval(user);

    await user.click(within(dialog).getByRole("button", { name: "Approve" }));

    const closes = await within(dialog).findByLabelText("Trading closes");
    expect(closes).toBeInvalid();
    expect(closes).toHaveAccessibleDescription(/Trading has to close in the future\./);
    // The fields scroll under the buttons: the refused one is brought to the user.
    expect(closes).toHaveFocus();
    expect(within(dialog).getByRole("button", { name: "Edit wording and times" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
  });

  test("a budget the API cannot read is said on the budget field", async () => {
    const { user } = show(proposed(), {
      [APPROVE]: apiError(422, "VALIDATION_ERROR", "The request is not valid.", {
        errors: [{ loc: ["body", "budget_cr"], msg: "String should match pattern", type: "x" }],
      }),
    });
    const dialog = await openDialog(user, "Approve", "dialog", "Approve market");

    await user.click(within(dialog).getByRole("radio", { name: "Custom" }));
    await user.type(within(dialog).getByLabelText("Loss budget"), "a lot");
    await user.click(within(dialog).getByRole("button", { name: "Approve" }));

    await waitFor(() => expect(within(dialog).getByLabelText("Loss budget")).toBeInvalid());
    expect(within(dialog).getByLabelText("Loss budget")).toHaveAccessibleDescription(
      /String should match pattern/,
    );
  });
});

describe("Reject", () => {
  test("asks for a reason in an alert-dialog that states the consequence, behind a destructive button", async () => {
    const rejected = adminMarket({
      status: "REJECTED",
      trading_open: false,
      tier: null,
      rejection_reason: "One trader can decide this outcome alone.",
    });
    const { api, user } = show(proposed(), { [REJECT]: json(200, rejected) });
    const dialog = await openDialog(user, "Reject", "alertdialog", "Reject proposal");

    expect(dialog).toHaveAccessibleDescription(
      "The proposer sees your reason. A rejected proposal stays rejected.",
    );
    const reject = within(dialog).getByRole("button", { name: "Reject" });
    expect(reject).toHaveAttribute("data-variant", "destructive");
    expect(reject).toBeDisabled();
    expect(within(dialog).getByLabelText("Reason")).toHaveAttribute("maxlength", "500");

    await user.type(
      within(dialog).getByLabelText("Reason"),
      "  One trader can decide this outcome alone. ",
    );
    await user.click(reject);

    await gone("alertdialog");
    expect(api.lastCall(REJECT)?.body).toEqual({
      reason: "One trader can decide this outcome alone.",
    });
    expect(status()).toHaveTextContent("Not approved");
    expect(screen.getByText("One trader can decide this outcome alone.")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Actions" })).not.toBeInTheDocument();
  });

  test("a reason of blanks is no reason", async () => {
    const { user } = show(proposed());
    const dialog = await openDialog(user, "Reject", "alertdialog", "Reject proposal");

    await user.type(within(dialog).getByLabelText("Reason"), "   ");

    expect(within(dialog).getByRole("button", { name: "Reject" })).toBeDisabled();
  });
});

describe("Halt, Resume and Close", () => {
  test("Halt asks for a reason and sends it; the market is paused afterwards", async () => {
    const { api, user } = show(adminMarket(), { [HALT]: json(200, halted()) });
    const dialog = await openDialog(user, "Halt", "dialog", "Halt market");

    const halt = within(dialog).getByRole("button", { name: "Halt" });
    expect(halt).toBeDisabled();
    expect(within(dialog).getByLabelText("Reason")).toHaveAccessibleDescription(
      "Kept in the audit log.",
    );

    await user.type(within(dialog).getByLabelText("Reason"), "The source is offline.");
    await user.click(halt);

    await gone("dialog");
    expect(api.lastCall(HALT)?.body).toEqual({ reason: "The source is offline." });
    expect(status()).toHaveTextContent("Paused");
    expect(bar()).toEqual(["Resume", "Close"]);
  });

  test("Resume goes ahead without a note, and then sends none", async () => {
    const { api, user } = show(halted(), { [RESUME]: json(200, adminMarket()) });
    const dialog = await openDialog(user, "Resume", "dialog", "Resume market");

    expect(within(dialog).getByLabelText("Note (optional)")).toHaveValue("");
    await user.click(within(dialog).getByRole("button", { name: "Resume" }));

    await gone("dialog");
    expect(api.lastCall(RESUME)?.rawBody).toBe("{}");
    expect(status()).toHaveTextContent("Open");
    expect(bar()).toEqual(["Halt", "Close"]);
  });

  test("Resume sends the note that was typed", async () => {
    const { api, user } = show(halted(), { [RESUME]: json(200, adminMarket()) });
    const dialog = await openDialog(user, "Resume", "dialog", "Resume market");

    await user.type(within(dialog).getByLabelText("Note (optional)"), "The source is back.");
    await user.click(within(dialog).getByRole("button", { name: "Resume" }));

    await gone("dialog");
    expect(api.lastCall(RESUME)?.body).toEqual({ note: "The source is back." });
  });

  test.each([
    ["an open", () => adminMarket()],
    ["a paused", halted],
  ] as const)("Close of %s market states that it cannot be taken back", async (_name, market) => {
    const { api, user } = show(market(), { [CLOSE]: json(200, closed()) });
    const dialog = await openDialog(user, "Close", "alertdialog", "Close market");

    expect(dialog).toHaveAccessibleDescription(
      "Trading stops for good. After this the market can only be resolved or voided.",
    );
    const close = within(dialog).getByRole("button", { name: "Close market" });
    expect(close).toHaveAttribute("data-variant", "primary");
    await user.click(close);

    await gone("alertdialog");
    expect(api.lastCall(CLOSE)?.rawBody).toBe("{}");
    expect(status()).toHaveTextContent("Closed");
    expect(bar()).toEqual(["Resolve", "Void"]);
  });
});

describe("Resolve", () => {
  const resolved = () =>
    adminMarket({
      status: "RESOLVED",
      trading_open: false,
      outcome: "YES",
      resolution: {
        outcome: "YES",
        evidence: "The operator's notices list no interruption.",
        resolved_at: "2026-10-15T08:30:00Z",
      },
    });

  test("asks for the outcome and the evidence, and neither outcome is chosen to begin with", async () => {
    const { api, user } = show(closed(), { [RESOLVE]: json(200, resolved()) });
    const dialog = await openDialog(user, "Resolve", "alertdialog", "Resolve market");

    expect(dialog).toHaveAccessibleDescription(
      "Everyone who holds the winning outcome is paid. This cannot be changed afterwards.",
    );
    const outcome = within(dialog).getByRole("group", { name: "Outcome" });
    const [yes, no] = within(outcome).getAllByRole("button");
    // YES stands on the left.
    expect(yes).toHaveTextContent("YES");
    expect(no).toHaveTextContent("NO");
    expect(yes).toHaveAttribute("aria-pressed", "false");
    expect(no).toHaveAttribute("aria-pressed", "false");
    expect(within(dialog).getByRole("button", { name: "Resolve" })).toBeDisabled();
    expect(within(dialog).getByLabelText("Evidence")).toHaveAttribute("maxlength", "2000");

    await user.click(yes!);
    expect(yes).toHaveAttribute("aria-pressed", "true");
    // The button names the outcome it will pay.
    const resolve = within(dialog).getByRole("button", { name: "Resolve YES" });
    expect(resolve).toBeDisabled();

    await user.type(
      within(dialog).getByLabelText("Evidence"),
      "The operator's notices list no interruption.",
    );
    expect(resolve).toBeEnabled();
    expect(resolve).toHaveAttribute("data-variant", "primary");
    await user.click(resolve);

    await gone("alertdialog");
    expect(api.lastCall(RESOLVE)?.body).toEqual({
      outcome: "YES",
      evidence: "The operator's notices list no interruption.",
    });
    expect(status()).toHaveTextContent("Resolved YES");
    expect(screen.getByRole("heading", { level: 2, name: "Resolution" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Actions" })).not.toBeInTheDocument();
  });

  test("the other outcome can be chosen instead, and is the one that is sent", async () => {
    const { api, user } = show(closed(), { [RESOLVE]: json(200, resolved()) });
    const dialog = await openDialog(user, "Resolve", "alertdialog", "Resolve market");

    await user.click(within(dialog).getByRole("button", { name: "YES" }));
    await user.click(within(dialog).getByRole("button", { name: "NO" }));
    expect(within(dialog).getByRole("button", { name: "YES" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    await user.type(within(dialog).getByLabelText("Evidence"), "An interruption was reported.");
    await user.click(within(dialog).getByRole("button", { name: "Resolve NO" }));

    await gone("alertdialog");
    expect(api.lastCall(RESOLVE)?.body).toEqual({
      outcome: "NO",
      evidence: "An interruption was reported.",
    });
  });

  test("answered FORBIDDEN all the same, the dialog shows the API's message and the market is read again", async () => {
    const { api, user } = show(closed(), { [RESOLVE]: apiError(403, "FORBIDDEN", TRADED) });
    const dialog = await openDialog(user, "Resolve", "alertdialog", "Resolve market");
    api.on(MARKET, json(200, closed({ viewer: marketViewer({ can_settle: false }) })));

    await user.click(within(dialog).getByRole("button", { name: "NO" }));
    await user.type(within(dialog).getByLabelText("Evidence"), "An interruption was reported.");
    await user.click(within(dialog).getByRole("button", { name: "Resolve NO" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(TRADED);
    await waitFor(() => expect(api.calls(MARKET)).toHaveLength(2));
    // The screen behind the dialog now says who may settle.
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Void", hidden: true })).toBeDisabled(),
    );
    expect(screen.getByRole("alertdialog", { name: "Resolve market" })).toBeInTheDocument();
  });
});

describe("Void", () => {
  const VOID_RULE = "If the market is voided, every trader gets their net stake back.";
  const voided = () =>
    adminMarket({
      status: "VOID",
      trading_open: false,
      outcome: "VOID",
      resolution: {
        outcome: "VOID",
        evidence: "The source was discontinued.",
        resolved_at: "2026-10-15T08:30:00Z",
      },
    });
  const costing = (void_cost_cr: string) =>
    json(200, risk({ markets: [riskMarketRow({ id: MARKET_ID, void_cost_cr })] }));

  test("states the market's void rule and what a void costs VIS now, and sends the reason", async () => {
    const { api, user } = show(closed(), { [VOID]: json(200, voided()), [RISK]: costing("12.5") });
    const dialog = await openDialog(user, "Void", "alertdialog", "Void market");

    expect(dialog).toHaveAccessibleDescription(VOID_RULE);
    const cost = within(dialog).getByText("What a void costs VIS now").parentElement!;
    expect(await within(cost).findByText("12.5 tokens")).toBeInTheDocument();
    const confirm = within(dialog).getByRole("button", { name: "Void" });
    expect(confirm).toHaveAttribute("data-variant", "destructive");
    expect(confirm).toBeDisabled();
    expect(within(dialog).getByLabelText("Reason")).toHaveAccessibleDescription(
      "Published as the market's resolution.",
    );
    expect(within(dialog).getByLabelText("Reason")).toHaveAttribute("maxlength", "2000");

    await user.type(within(dialog).getByLabelText("Reason"), "The source was discontinued.");
    await user.click(confirm);

    await gone("alertdialog");
    expect(api.lastCall(VOID)?.body).toEqual({ reason: "The source was discontinued." });
    expect(status()).toHaveTextContent("Voided");
    expect(screen.queryByRole("region", { name: "Actions" })).not.toBeInTheDocument();
  });

  test("the cost is read afresh when the dialog opens: a figure from an earlier read is not shown, and nothing can be confirmed meanwhile", async () => {
    const fresh = deferred();
    const api = mockApi({
      [MARKET]: json(200, closed()),
      [ME]: json(200, adminUser),
      [RISK]: fresh.handler,
    });
    const client = createTestQueryClient();
    client.setQueryData(
      keys.admin.risk,
      risk({ markets: [riskMarketRow({ id: MARKET_ID, void_cost_cr: "999" })] }),
    );
    const { user } = renderWithProviders(<AdminMarketScreen marketId={MARKET_ID} />, {
      me: adminUser,
      client,
    });
    const dialog = await openDialog(user, "Void", "alertdialog", "Void market");
    await user.type(within(dialog).getByLabelText("Reason"), "The source was discontinued.");

    await waitFor(() => expect(api.calls(RISK)).toHaveLength(1));
    const cost = within(dialog).getByText("What a void costs VIS now").parentElement!;
    expect(within(cost).queryByText(/999/)).not.toBeInTheDocument();
    expect(cost.querySelector("[data-slot=skeleton]")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Void" })).toBeDisabled();

    fresh.resolve(costing("12.5"));

    expect(await within(cost).findByText("12.5 tokens")).toBeInTheDocument();
    expect(cost.querySelector("[data-slot=skeleton]")).not.toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Void" })).toBeEnabled();
  });

  test("a cost the API sends with a minus sign keeps it", async () => {
    const { user } = show(closed(), { [RISK]: costing("-130.752819") });
    const dialog = await openDialog(user, "Void", "alertdialog", "Void market");

    const cost = within(dialog).getByText("What a void costs VIS now").parentElement!;
    expect(await within(cost).findByText("−130.75 tokens")).toBeInTheDocument();
  });

  test("when the cost cannot be read, the dialog shows the banner and cannot be confirmed", async () => {
    const { api, user } = show(closed(), {
      [RISK]: apiError(
        503,
        "SERVICE_UNAVAILABLE",
        "This is temporarily unavailable. Try again shortly.",
      ),
    });
    const dialog = await openDialog(user, "Void", "alertdialog", "Void market");
    await user.type(within(dialog).getByLabelText("Reason"), "The source was discontinued.");

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "This is temporarily unavailable. Try again shortly.",
    );
    expect(within(dialog).getByRole("button", { name: "Void" })).toBeDisabled();
    expect(api.calls(VOID)).toHaveLength(0);

    api.on(RISK, costing("12.5"));
    await user.click(within(dialog).getByRole("button", { name: "Try again" }));

    expect(await within(dialog).findByText("12.5 tokens")).toBeInTheDocument();
    expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Void" })).toBeEnabled();
  });

  test("a market that is missing from the risk figures was settled meanwhile: nothing can be confirmed and the market is read again", async () => {
    const { api, user } = show(closed(), { [RISK]: json(200, risk({ markets: [] })) });
    const dialog = await openDialog(user, "Void", "alertdialog", "Void market");
    await user.type(within(dialog).getByLabelText("Reason"), "The source was discontinued.");

    const cost = within(dialog).getByText("What a void costs VIS now").parentElement!;
    expect(await within(cost).findByText("none yet")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Void" })).toBeDisabled();
    await waitFor(() => expect(api.calls(MARKET)).toHaveLength(2));
  });
});

describe("every dialog", () => {
  test("CONFLICT shows the API's message in the dialog and reads the market again; the dialog stays although the market changed behind it", async () => {
    const { api, user } = show(adminMarket(), { [CLOSE]: apiError(409, "CONFLICT", NOT_POSSIBLE) });
    const dialog = await openDialog(user, "Close", "alertdialog", "Close market");
    api.on(MARKET, json(200, closed()));

    await user.click(within(dialog).getByRole("button", { name: "Close market" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(NOT_POSSIBLE);
    await waitFor(() => expect(api.calls(MARKET)).toHaveLength(2));
    // Behind the dialog the bar shows what the market allows now.
    expect(
      await screen.findByRole("button", { name: "Resolve", hidden: true }),
    ).toBeInTheDocument();
    expect(screen.getByRole("alertdialog", { name: "Close market" })).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

    await gone("alertdialog");
    expect(status()).toHaveTextContent("Closed");
    expect(bar()).toEqual(["Resolve", "Void"]);
  });

  test("the button that goes ahead is disabled from tap until the answer: two taps are one action, and the dialog cannot be closed meanwhile", async () => {
    const answer = deferred();
    const api = mockApi({
      [MARKET]: json(200, adminMarket()),
      [ME]: json(200, adminUser),
      [HALT]: answer.handler,
    });
    const { user } = renderWithProviders(<AdminMarketScreen marketId={MARKET_ID} />, {
      me: adminUser,
    });
    const dialog = await openDialog(user, "Halt", "dialog", "Halt market");

    await user.type(within(dialog).getByLabelText("Reason"), "The source is offline.");
    await user.click(within(dialog).getByRole("button", { name: "Halt" }));
    await user.click(within(dialog).getByRole("button", { name: "Halt" }));
    await user.keyboard("{Enter}");

    const halt = within(dialog).getByRole("button", { name: "Halt" });
    expect(halt).toBeDisabled();
    expect(halt).toHaveAttribute("aria-busy", "true");
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toBeDisabled();
    // The close cross stays in its place, disabled: it is never taken away.
    expect(within(dialog).getByRole("button", { name: "Close" })).toBeDisabled();
    await user.keyboard("{Escape}");
    expect(screen.getByRole("dialog", { name: "Halt market" })).toBeInTheDocument();
    expect(api.calls(HALT)).toHaveLength(1);

    answer.resolve(json(200, halted()));
    await gone("dialog");
    expect(api.calls(HALT)).toHaveLength(1);
  });

  test("without an answer the button waits until the market was read again, then the dialog says what happened", async () => {
    const reread = deferred();
    const { api, user } = show(adminMarket());
    api.on(HALT, noAnswer);
    const dialog = await openDialog(user, "Halt", "dialog", "Halt market");
    api.on(MARKET, reread.handler);

    await user.type(within(dialog).getByLabelText("Reason"), "The source is offline.");
    await user.click(within(dialog).getByRole("button", { name: "Halt" }));

    // The request failed; the market is on its way: nothing can be sent meanwhile.
    await waitFor(() => expect(api.calls(MARKET)).toHaveLength(2));
    expect(within(dialog).getByRole("button", { name: "Halt" })).toHaveAttribute(
      "aria-busy",
      "true",
    );
    expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Halt" }));
    expect(api.calls(HALT)).toHaveLength(1);

    // The halt did arrive: the screen shows what the server recorded.
    reread.resolve(json(200, halted()));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent("No connection. Try again.");
    expect(await screen.findByRole("button", { name: "Resume", hidden: true })).toBeInTheDocument();
    expect(api.calls(HALT)).toHaveLength(1);
  });

  test("an error on the text appears on its field with the API's message, and goes when the text changes", async () => {
    const { user } = show(adminMarket(), {
      [HALT]: apiError(422, "VALIDATION_ERROR", "This text has a character that cannot be saved.", {
        field: "reason",
      }),
    });
    const dialog = await openDialog(user, "Halt", "dialog", "Halt market");

    await user.type(within(dialog).getByLabelText("Reason"), "The source is offline.");
    await user.click(within(dialog).getByRole("button", { name: "Halt" }));

    await waitFor(() => expect(within(dialog).getByLabelText("Reason")).toBeInvalid());
    expect(within(dialog).getByLabelText("Reason")).toHaveAccessibleDescription(
      /This text has a character that cannot be saved\./,
    );

    await user.type(within(dialog).getByLabelText("Reason"), "!");

    expect(within(dialog).getByLabelText("Reason")).not.toBeInvalid();
    expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();
  });

  test("after RATE_LIMITED the button is disabled for the time the API names, with a countdown", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const { user } = show(adminMarket(), {
        [HALT]: apiError(429, "RATE_LIMITED", "Too many attempts. Try again later.", {
          retry_after_seconds: 15,
        }),
      });
      const dialog = await openDialog(user, "Halt", "dialog", "Halt market");

      await user.type(within(dialog).getByLabelText("Reason"), "The source is offline.");
      await user.click(within(dialog).getByRole("button", { name: "Halt" }));

      expect(await within(dialog).findByRole("alert")).toHaveTextContent(
        "Too many attempts. Try again later.",
      );
      expect(within(dialog).getByRole("button", { name: "Halt" })).toBeDisabled();
      expect(within(dialog).getByText(/^Try again in \d+ s$/)).toBeInTheDocument();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(16_000);
      });

      expect(within(dialog).getByRole("button", { name: "Halt" })).toBeEnabled();
    } finally {
      vi.useRealTimers();
    }
  });

  test("a dialog that is opened again starts empty, without the answer of the time before", async () => {
    const { user } = show(adminMarket(), { [HALT]: apiError(409, "CONFLICT", NOT_POSSIBLE) });
    const dialog = await openDialog(user, "Halt", "dialog", "Halt market");
    await user.type(within(dialog).getByLabelText("Reason"), "The source is offline.");
    await user.click(within(dialog).getByRole("button", { name: "Halt" }));
    await within(dialog).findByRole("alert");
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await gone("dialog");

    const again = await openDialog(user, "Halt", "dialog", "Halt market");

    expect(within(again).getByLabelText("Reason")).toHaveValue("");
    expect(within(again).queryByRole("alert")).not.toBeInTheDocument();
  });

  test("closing a dialog gives the focus back to the button that opened it", async () => {
    const { user } = show(adminMarket());
    const dialog = await openDialog(user, "Halt", "dialog", "Halt market");

    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

    await gone("dialog");
    await waitFor(() => expect(screen.getByRole("button", { name: "Halt" })).toHaveFocus());
  });

  test("after an action took its own button off the screen, the focus is on the question", async () => {
    const { user } = show(adminMarket(), { [HALT]: json(200, halted()) });
    const dialog = await openDialog(user, "Halt", "dialog", "Halt market");

    await user.type(within(dialog).getByLabelText("Reason"), "The source is offline.");
    await user.click(within(dialog).getByRole("button", { name: "Halt" }));

    await gone("dialog");
    await waitFor(() => expect(screen.getByRole("heading", { level: 1 })).toHaveFocus());
  });
});
