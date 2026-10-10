import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import type { RiskResponse } from "@/lib/api/schema";
import { alert, MARKET_ID, me, risk, riskMarketRow } from "@/test/fixtures";
import { apiError, deferred, json, mockApi, noAnswer, type Route } from "@/test/http";
import { navigation } from "@/test/navigation";
import { renderWithProviders } from "@/test/render";

import { AdminRiskScreen } from "./admin-risk-screen";

const RISK: Route = "GET /api/admin/risk";
const ACK: Route = "POST /api/admin/alerts/{alert_id}/ack";

const admin = { me: me({ role: "ADMIN" }) };

function show(answer: RiskResponse = risk()) {
  navigation.go("/admin/risk");
  const api = mockApi({ [RISK]: json(200, answer) });
  const rendered = renderWithProviders(<AdminRiskScreen />, admin);
  return { api, ...rendered };
}

const tile = (name: string) => screen.findByRole("group", { name });
/** What a screen reader hears of a tile, without what only the eye gets. */
function spoken(element: HTMLElement): string {
  const copy = element.cloneNode(true) as HTMLElement;
  for (const hidden of copy.querySelectorAll("[aria-hidden]")) hidden.remove();
  // A heading, a paragraph, a label and its value, and a line of a cell that has two are
  // each heard on their own.
  for (const block of copy.querySelectorAll("h3, p, dt, dd, .block")) block.append(" ");
  return (copy.textContent ?? "").replace(/\s+/g, " ").trim();
}

describe("Admin: risk", () => {
  test("stands under the tabs, with a hidden h1 that names the screen", async () => {
    show();

    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Risk");
    expect(screen.getByRole("link", { name: "Risk" })).toHaveAttribute("aria-current", "page");
    expect(await screen.findByRole("heading", { level: 2, name: "Alerts" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "Markets" })).toBeInTheDocument();
  });

  test("the tiles show every figure as the API sent it, each beside its limit", async () => {
    show(
      risk({
        obligations: {
          total_cr: "48211.4",
          alert_threshold_cr: "100000",
          above_threshold: false,
          user_balances_cr: "46300",
          pending_vouchers_cr: "1500",
          market_exposure_cr: "411.4",
        },
        loss_budget_cr: "4158.883086",
        max_loss_budget_cr: "70000",
        open_markets: 6,
        max_open_markets: 100,
        unredeemed_code_value_cr: "490",
        code_issuance_this_month_cr: "700",
        max_code_issuance_per_month_cr: "7000",
        adjustments_this_month_cr: "35",
      }),
    );

    expect(spoken(await tile("Obligations"))).toBe(
      "Obligations 48,211.4 tokens Alert above 100,000 tokens Balances 46,300 tokens Vouchers not used yet 1,500 tokens Markets 411.4 tokens",
    );
    // Cut after the second decimal, never rounded up.
    expect(spoken(await tile("Loss budget"))).toBe("Loss budget 4,158.88 tokens of 70,000 tokens");
    expect(spoken(await tile("Open markets"))).toBe("Open markets 6 of 100");
    expect(spoken(await tile("Codes not redeemed yet"))).toBe("Codes not redeemed yet 490 tokens");
    // The month's net adjustments stand under the two, and have no "of": no cap counts them.
    expect(spoken(await tile("Codes this month"))).toBe(
      "Codes this month 700 tokens of 7,000 tokens Adjustments this month 35 tokens",
    );
    expect(screen.getAllByRole("group")).toHaveLength(5);
  });

  test("the net adjustments of the month are the API's figure, sign included", async () => {
    const { unmount } = show(risk({ adjustments_this_month_cr: "-12.512345" }));
    const taken = (await tile("Codes this month")).querySelector("dl")!;
    expect(spoken(taken)).toBe("Adjustments this month −12.51 tokens");
    // More taken than given is no gain and no loss of anybody: it is not coloured.
    expect(taken.querySelector("[data-slot=amount]")).not.toHaveClass("text-yes-text");
    unmount();

    show(risk({ adjustments_this_month_cr: "0" }));
    expect(spoken((await tile("Codes this month")).querySelector("dl")!)).toBe(
      "Adjustments this month 0 tokens",
    );
  });

  test("the parts of the obligations stand under their labels", async () => {
    show();

    const obligations = await tile("Obligations");
    const parts = Array.from(obligations.querySelectorAll("dt")).map((label) => [
      label.textContent,
      label.parentElement?.querySelector("dd .sr-only")?.textContent,
    ]);
    expect(parts).toEqual([
      ["Balances", "46,300 tokens"],
      ["Vouchers not used yet", "1,500 tokens"],
      ["Markets", "411.4 tokens"],
    ]);
  });

  test("only the flag of the API puts the Obligations tile into its warning state", async () => {
    // A total far above its threshold, and no flag: the page compares nothing.
    const { unmount } = show(
      risk({
        obligations: {
          total_cr: "999999",
          alert_threshold_cr: "1",
          above_threshold: false,
          user_balances_cr: "999999",
          pending_vouchers_cr: "0",
          market_exposure_cr: "0",
        },
      }),
    );
    const calm = await tile("Obligations");
    expect(calm).not.toHaveClass("border-no");
    expect(calm.querySelector("h3 svg")).toBeNull();
    unmount();

    show(
      risk({
        obligations: {
          total_cr: "5",
          alert_threshold_cr: "100000",
          above_threshold: true,
          user_balances_cr: "5",
          pending_vouchers_cr: "0",
          market_exposure_cr: "0",
        },
      }),
    );
    const flagged = await screen.findByRole("group", {
      name: "Obligations, above the alert threshold",
    });
    expect(flagged).toHaveClass("border-no");
    expect(flagged.querySelector("h3 svg")).toBeInTheDocument();
  });

  test("no other tile has a warning state, whatever its figures", async () => {
    show(
      risk({
        loss_budget_cr: "80000",
        max_loss_budget_cr: "70000",
        open_markets: 150,
        max_open_markets: 100,
        code_issuance_this_month_cr: "9000",
        max_code_issuance_per_month_cr: "7000",
      }),
    );

    for (const name of ["Loss budget", "Open markets", "Codes this month"]) {
      const calm = await tile(name);
      expect(calm).not.toHaveClass("border-no");
      expect(calm.querySelector("svg.lucide")).toBeNull();
    }
    expect(spoken(await tile("Open markets"))).toBe("Open markets 150 of 100");
  });

  test("a figure the API sends with a minus sign keeps it", async () => {
    show(
      risk({
        obligations: {
          total_cr: "-12.345678",
          alert_threshold_cr: "100000",
          above_threshold: false,
          user_balances_cr: "0",
          pending_vouchers_cr: "0",
          market_exposure_cr: "-12.345678",
        },
      }),
    );

    expect(spoken(await tile("Obligations"))).toContain("Obligations −12.34 tokens");
    expect(spoken(await tile("Obligations"))).toContain("Markets −12.34 tokens");
  });

  test("shows skeletons in the shape of the page while it loads", async () => {
    const answer = deferred();
    navigation.go("/admin/risk");
    mockApi({ [RISK]: answer.handler });
    renderWithProviders(<AdminRiskScreen />, admin);

    expect(document.querySelector("[data-slot=risk-skeleton]")).toBeInTheDocument();
    expect(screen.queryByText("No alerts.")).not.toBeInTheDocument();

    answer.resolve(json(200, risk()));
    await waitFor(() =>
      expect(document.querySelector("[data-slot=risk-skeleton]")).not.toBeInTheDocument(),
    );
  });

  test("a page that cannot be read shows the API's message with Try again", async () => {
    navigation.go("/admin/risk");
    const api = mockApi({
      [RISK]: apiError(
        503,
        "SERVICE_UNAVAILABLE",
        "This is temporarily unavailable. Try again shortly.",
      ),
    });
    const { user } = renderWithProviders(<AdminRiskScreen />, admin);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This is temporarily unavailable. Try again shortly.",
    );

    api.on(RISK, json(200, risk()));
    await user.click(screen.getByRole("button", { name: "Try again" }));

    expect(await tile("Obligations")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  test("it is read again when the window gets the focus back", async () => {
    const { api } = show();
    // Everything on screen is read again then, the balance in the top bar included.
    api.on("GET /api/me", json(200, admin.me));
    await tile("Obligations");
    expect(api.calls(RISK)).toHaveLength(1);

    window.dispatchEvent(new Event("visibilitychange"));

    await waitFor(() => expect(api.calls(RISK)).toHaveLength(2));
  });
});

describe("alerts", () => {
  test("without one: a plain sentence", async () => {
    show(risk({ alerts: [] }));

    expect(await screen.findByText("No alerts.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Acknowledge" })).not.toBeInTheDocument();
  });

  test("each shows its message, when it was raised and the keys and values of its context", async () => {
    show(
      risk({
        alerts: [
          alert({
            id: 7,
            severity: "WARNING",
            message: "Obligations are above the alert threshold.",
            created_at: "2026-10-04T11:00:00Z",
            context: {
              total_cr: "100250",
              market_id: MARKET_ID,
              attempts: 3,
              halted: true,
              nothing: null,
              details: { invariant: "ledger_nets_to_zero" },
            },
          }),
        ],
      }),
    );

    const item = (await screen.findByText("Obligations are above the alert threshold.")).closest(
      "li",
    )!;
    expect(within(item).getByText("4 Oct 2026, 13:00")).toBeInTheDocument();
    const context = Array.from(item.querySelectorAll("dt")).map((key) => [
      key.textContent,
      key.parentElement?.querySelector("dd")?.textContent,
    ]);
    expect(context).toEqual([
      ["total_cr", "100250"],
      ["market_id", MARKET_ID],
      ["attempts", "3"],
      ["halted", "true"],
      ["nothing", "null"],
      ["details", '{"invariant":"ledger_nets_to_zero"}'],
    ]);
    expect(within(item).getByRole("button", { name: "Acknowledge" })).toBeEnabled();
  });

  test("INFO and WARNING are neutral banners with different icons; CRITICAL is the error banner", async () => {
    show(
      risk({
        alerts: [
          alert({ id: 1, severity: "INFO", message: "A grant run finished." }),
          alert({ id: 2, severity: "WARNING", message: "Obligations are high." }),
          alert({ id: 3, severity: "CRITICAL", message: "A market was halted." }),
        ],
      }),
    );

    const banner = async (message: string) =>
      (await screen.findByText(message)).closest<HTMLElement>("[data-slot=alert]")!;
    const info = await banner("A grant run finished.");
    const warning = await banner("Obligations are high.");
    const critical = await banner("A market was halted.");

    expect(info).toHaveAttribute("data-variant", "neutral");
    expect(warning).toHaveAttribute("data-variant", "neutral");
    expect(critical).toHaveAttribute("data-variant", "error");
    const icons = [info, warning, critical].map((element) =>
      element.querySelector(":scope > svg")?.getAttribute("class"),
    );
    expect(new Set(icons).size).toBe(3);
    // The word is for a screen reader, which sees neither the icon nor the colour.
    expect(info).toHaveTextContent("Information: A grant run finished.");
    expect(warning).toHaveTextContent("Warning: Obligations are high.");
    expect(critical).toHaveTextContent("Critical: A market was halted.");
    expect(within(critical).getByText("Critical:")).toHaveClass("sr-only");
  });

  test("Acknowledge sends the alert's id, and the alert is gone once the page was read again", async () => {
    const { api, user } = show(
      risk({
        alerts: [
          alert({ id: 7, message: "The first alert." }),
          alert({ id: 9, message: "The second alert." }),
        ],
      }),
    );
    api.on(ACK, json(200, alert({ id: 7, acked_at: "2026-10-04T12:00:00Z" })));
    const first = (await screen.findByText("The first alert.")).closest("li")!;
    api.on(RISK, json(200, risk({ alerts: [alert({ id: 9, message: "The second alert." })] })));

    await user.click(within(first).getByRole("button", { name: "Acknowledge" }));

    await waitFor(() => expect(screen.queryByText("The first alert.")).not.toBeInTheDocument());
    expect(api.lastCall(ACK)?.path).toBe("/api/admin/alerts/7/ack");
    expect(api.calls(ACK)).toHaveLength(1);
    await waitFor(() => expect(api.calls(RISK)).toHaveLength(2));
    expect(screen.getByText("The second alert.")).toBeInTheDocument();
  });

  test("the button is disabled from tap until the answer: two taps are one request", async () => {
    const answer = deferred();
    const { api, user } = show(risk({ alerts: [alert({ id: 7 })] }));
    api.on(ACK, answer.handler);
    const button = await screen.findByRole("button", { name: "Acknowledge" });

    await user.click(button);
    await user.click(button);

    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("aria-busy", "true");
    expect(api.calls(ACK)).toHaveLength(1);

    api.on(RISK, json(200, risk({ alerts: [] })));
    answer.resolve(json(200, alert({ id: 7, acked_at: "2026-10-04T12:00:00Z" })));
    expect(await screen.findByText("No alerts.")).toBeInTheDocument();
  });

  test("two taps in one moment are one request: the second is stopped where it is made", async () => {
    const answer = deferred();
    const { api } = show(risk({ alerts: [alert({ id: 7 })] }));
    api.on(ACK, answer.handler);
    const button = await screen.findByRole("button", { name: "Acknowledge" });

    // A tap that lands twice: no render lies between the two, so the button was not
    // disabled for the second.
    act(() => {
      button.click();
      button.click();
    });

    await waitFor(() => expect(api.calls(ACK)).toHaveLength(1));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(api.calls(ACK)).toHaveLength(1);
    expect(button).toHaveAttribute("aria-busy", "true");
  });

  test("a refusal shows the API's message beside the alert, which stays", async () => {
    const { api, user } = show(risk({ alerts: [alert({ id: 7, message: "The alert." })] }));
    api.on(ACK, apiError(404, "NOT_FOUND", "Not found."));
    const item = (await screen.findByText("The alert.")).closest("li")!;

    await user.click(within(item).getByRole("button", { name: "Acknowledge" }));

    expect(await within(item).findByText("Not found.")).toBeInTheDocument();
    expect(within(item).getByRole("button", { name: "Acknowledge" })).toBeEnabled();
  });

  test("without an answer the page is read again before the alert can be acknowledged once more", async () => {
    const reread = deferred();
    const { api, user } = show(risk({ alerts: [alert({ id: 7, message: "The alert." })] }));
    api.on(ACK, noAnswer);
    const item = (await screen.findByText("The alert.")).closest("li")!;
    api.on(RISK, reread.handler);

    await user.click(within(item).getByRole("button", { name: "Acknowledge" }));

    await waitFor(() => expect(api.calls(RISK)).toHaveLength(2));
    expect(within(item).getByRole("button", { name: "Acknowledge" })).toHaveAttribute(
      "aria-busy",
      "true",
    );

    reread.resolve(json(200, risk({ alerts: [alert({ id: 7, message: "The alert." })] })));

    expect(await within(item).findByText("No connection. Try again.")).toBeInTheDocument();
    expect(within(item).getByRole("button", { name: "Acknowledge" })).toBeEnabled();
    expect(api.calls(ACK)).toHaveLength(1);
  });
});

describe("the markets that are not settled", () => {
  test("without one: a plain sentence", async () => {
    show(risk({ markets: [] }));

    expect(await screen.findByText("No markets.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  test("one row per market, in the API's order, in ten columns with every figure as sent", async () => {
    show(
      risk({
        markets: [
          riskMarketRow({
            id: "second",
            question: "Closes later, listed first",
            close_at: "2026-12-01T16:00:00Z",
          }),
          riskMarketRow({
            id: MARKET_ID,
            question: "Will the Polybahn run without interruption?",
            status: "HALTED",
            tier: "CUSTOM",
            p_yes: 0.548,
            volume_cr: "1250.5",
            q_yes: "412.5",
            q_no: "220.257999",
            b_cr: "1000",
            budget_cr: "693.147181",
            account_balance_cr: "823.9",
            exposure_cr: "-411.4",
            void_cost_cr: "-130.752819",
            close_at: "2026-10-14T16:00:00Z",
          }),
        ],
      }),
    );

    const table = await screen.findByRole("table", { name: "Markets" });
    // Exposure and void cost stand before the share counts: an admin reads them first.
    expect(
      within(table)
        .getAllByRole("columnheader")
        .map((header) => header.textContent),
    ).toEqual([
      "Question",
      "Chance",
      "Volume",
      "Exposure",
      "Void cost",
      "Shares",
      "Tier",
      "Loss budget",
      "Account",
      "Closes",
    ]);
    const [, first, second] = within(table).getAllByRole("row");
    expect(within(first!).getAllByRole("cell")[0]).toHaveTextContent("Closes later, listed first");
    expect(
      within(second!)
        .getAllByRole("cell")
        .map((cell) => spoken(cell)),
    ).toEqual([
      // The status badge stands under the question, in its cell.
      "Will the Polybahn run without interruption? Paused",
      "54.8%",
      "1,250.5 tokens",
      "−411.4 tokens",
      "−130.75 tokens",
      // Both share counts in one cell, one under the other. Cut, not rounded.
      "412.5 YES 220.25 NO",
      // The tier, and under it the liquidity b it stands for.
      "Custom b 1,000",
      "693.14 tokens",
      "823.9 tokens",
      // The date, and under it the time.
      "14 Oct 2026 18:00",
    ]);
    const question = within(second!).getAllByRole("cell")[0]!;
    expect(within(question).getByText("Paused")).toHaveAttribute("data-slot", "badge");
  });

  test("the table is built to fit 1024 px: ten columns, 8 px at the sides of every cell, and a question that gives way", async () => {
    show(risk({ markets: [riskMarketRow()] }));

    const table = await screen.findByRole("table", { name: "Markets" });
    const headings = within(table).getAllByRole("columnheader");
    const [, row] = within(table).getAllByRole("row");
    const cells = within(row!).getAllByRole("cell");
    expect(headings).toHaveLength(10);
    expect(cells).toHaveLength(10);
    for (const cell of [...headings, ...cells]) expect(cell).toHaveClass("px-2");
    // The question wraps and takes the room the others leave; no other column gives way.
    expect(cells[0]).toHaveClass("wrap-break-word");
    for (const cell of cells.slice(1)) expect(cell).toHaveClass("whitespace-nowrap", "w-px");
    // Figures are right aligned, in the number style.
    for (const index of [1, 2, 3, 4, 5, 7, 8]) {
      expect(cells[index]).toHaveClass("text-right", "type-number");
    }
    // The second lines are captions.
    expect(within(cells[6]!).getByText("b 1,000")).toHaveClass("type-caption");
    expect(within(cells[9]!).getByText("18:00")).toHaveClass("type-caption");
  });

  test("a market without a tier shows a dash", async () => {
    show(risk({ markets: [riskMarketRow({ tier: null })] }));

    const table = await screen.findByRole("table", { name: "Markets" });
    const [, row] = within(table).getAllByRole("row");
    const tier = within(row!).getAllByRole("cell")[6]!;
    expect(spoken(tier)).toBe("none yet");
    // Without a tier there is no liquidity to name under it.
    expect(tier).not.toHaveTextContent(/b /);
  });

  test("a row opens the admin view of its market", async () => {
    const { user } = show(risk({ markets: [riskMarketRow({ id: MARKET_ID })] }));
    const table = await screen.findByRole("table", { name: "Markets" });

    const link = within(table).getByRole("link");
    expect(link).toHaveAttribute("href", `/admin/markets/${MARKET_ID}`);
    await user.click(within(table).getByText("54.8%"));

    expect(navigation.pathname).toBe(`/admin/markets/${MARKET_ID}`);
  });
});
