import { useQuery } from "@tanstack/react-query";
import { screen } from "@testing-library/react";
import { useState } from "react";

import { ResponsiveSheet } from "@/components/sheet";
import { keys } from "@/lib/api/keys";
import { marketQuery } from "@/lib/api/queries";
import type { MarketDetailResponse, PublicConfigResponse } from "@/lib/api/schema";
import { config, MARKET_ID, marketDetail, me } from "@/test/fixtures";
import { type Handler, json, mockApi, type MockRequest, type Route } from "@/test/http";
import { createTestQueryClient, renderWithProviders } from "@/test/render";

import { type TradeIntent, TradeTicket } from "./trade-ticket";

/**
 * What the tests of the trade ticket share: a stand-in for Market detail, which owns the
 * ticket's surroundings, and the words to find the ticket's controls by. Only tests
 * import this file.
 */

export const MARKET: Route = "GET /api/markets/{market_id}";
export const QUOTE: Route = "GET /api/markets/{market_id}/quote";
export const TRADE: Route = "POST /api/markets/{market_id}/trades";
export const ME: Route = "GET /api/me";

export const BUY_YES: TradeIntent = { side: "BUY", outcome: "YES" };
export const BUY_NO: TradeIntent = { side: "BUY", outcome: "NO" };
export const SELL_YES: TradeIntent = { side: "SELL", outcome: "YES" };
export const SELL_NO: TradeIntent = { side: "SELL", outcome: "NO" };

const OPENERS = [
  ["Open on Buy YES", BUY_YES],
  ["Open on Buy NO", BUY_NO],
  ["Open on Sell YES", SELL_YES],
  ["Open on Sell NO", SELL_NO],
] as const;

/**
 * Where the ticket stands. `bare`: nothing around it, mounted while an intent is set.
 * `sheet`: in the `ResponsiveSheet` of the app, a bottom sheet that is a dialog from
 * 768 px. `closing-sheet`: a sheet that takes its time to go, as a real one does while it
 * slides away: when the ticket is done it stays until "Close the sheet". `panel`: always
 * there, as the sticky panel from 1024 px; "Done" starts over on Buy and YES.
 */
type Surface = "bare" | "sheet" | "closing-sheet" | "panel";

function MarketDetailStandIn({
  start,
  surface,
  changes,
}: {
  start: TradeIntent | null;
  surface: Surface;
  changes: (TradeIntent | null)[];
}) {
  const market = useQuery(marketQuery(MARKET_ID));
  const [intent, setIntent] = useState<TradeIntent | null>(start);
  if (market.data === undefined) return null;

  const ticket = intent !== null && (
    <TradeTicket
      market={market.data}
      intent={intent}
      onIntentChange={(next) => {
        changes.push(next);
        if (surface === "panel") setIntent(next ?? BUY_YES);
        else if (surface !== "closing-sheet" || next !== null) setIntent(next);
      }}
    />
  );

  return (
    <>
      {OPENERS.map(([label, opened]) => (
        <button key={label} type="button" onClick={() => setIntent(opened)}>
          {label}
        </button>
      ))}
      <button type="button" onClick={() => setIntent(null)}>
        Close the sheet
      </button>
      {surface === "sheet" ? (
        <ResponsiveSheet
          open={intent !== null}
          onOpenChange={(open) => {
            if (!open) setIntent(null);
          }}
          title="Trade"
          hideTitle
        >
          {ticket}
        </ResponsiveSheet>
      ) : surface === "closing-sheet" ? (
        intent !== null && (
          <div role="dialog" aria-label="Trade">
            {ticket}
          </div>
        )
      ) : (
        ticket
      )}
    </>
  );
}

export type TicketOptions = {
  /** What `GET /markets/{market_id}` answers, and what is on screen when the test starts. */
  market?: MarketDetailResponse;
  /** The intent the sheet is opened with, or null to start with it closed. */
  start?: TradeIntent | null;
  surface?: Surface;
  routes?: Partial<Record<Route, Handler>>;
  tunables?: Partial<PublicConfigResponse>;
};

/**
 * Renders the ticket as Market detail shows it, with the market already on screen. The
 * quote is asked for almost at once: a test of the debounce sets `quote_debounce_ms`.
 */
export function openTicket({
  market = marketDetail(),
  start = BUY_YES,
  surface = "bare",
  routes = {},
  tunables = {},
}: TicketOptions = {}) {
  // An opening ticket reads the market and the caller's balance again.
  const api = mockApi({ [MARKET]: json(200, market), [ME]: json(200, me()), ...routes });
  const client = createTestQueryClient();
  client.setQueryData(keys.markets.detail(MARKET_ID), market);
  const changes: (TradeIntent | null)[] = [];

  const view = renderWithProviders(
    <MarketDetailStandIn start={start} surface={surface} changes={changes} />,
    { client, config: config({ quote_debounce_ms: 1, ...tunables }) },
  );
  return { ...view, api, changes };
}

/* ---- The ticket's controls, by what a user reads on them ---------------------------- */

export const ticketFrame = () => document.querySelector<HTMLElement>("[data-slot=trade-ticket]");
export const sideSegment = (name: "Buy" | "Sell") => screen.getByRole("radio", { name });
export const outcomeSegment = (name: "YES" | "NO") => screen.getByRole("button", { name });
/** A stake chip, by its amount: its name is the amount and the word, "25 tokens". */
export const stakeChip = (amount: string) =>
  screen.getByRole("button", { name: `${amount} tokens` });
export const sellChip = (portion: "Half" | "All") =>
  screen.getByRole("button", { name: new RegExp(`^${portion}`) });
export const button = (name: string) => screen.getByRole("button", { name });
/** The row of a label and its value: of the quote panel, of the receipt, the balance line. */
export const row = (label: string) => screen.getByText(label).parentElement!;

/* ---- What went out ------------------------------------------------------------------ */

/** The Idempotency-Key and the body of a trade request, exactly as they were sent. */
export function sent(request: MockRequest | undefined) {
  return {
    key: request?.headers.get("Idempotency-Key") ?? null,
    body: request?.rawBody ?? null,
  };
}
