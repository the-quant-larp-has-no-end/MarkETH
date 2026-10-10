import type { TradeTicketProps } from "@/features/trade";
import { history, MARKET_ID, marketDetail } from "@/test/fixtures";
import { json, mockApi, type Route } from "@/test/http";

/**
 * What the tests of this feature share. It is imported by tests only.
 */

export const MARKET: Route = "GET /api/markets/{market_id}";
export const HISTORY: Route = "GET /api/markets/{market_id}/history";
export const ME: Route = "GET /api/me";

export const PATH = `/markets/${MARKET_ID}`;

/** The API of an open market nobody has traded yet, with its three-point history. */
export function mockMarketApi(market = marketDetail(), points = history()) {
  return mockApi({ [MARKET]: json(200, market), [HISTORY]: json(200, points) });
}

/**
 * The trade ticket is another feature's. In its place stands this: it shows what it was
 * given and has a button for each thing a ticket can tell its owner.
 *
 *     vi.mock("@/features/trade", async () => (await import("./testing")).tradeFeature);
 */
function TicketStandIn({ market, intent, onIntentChange }: TradeTicketProps) {
  return (
    <div data-testid="ticket" data-side={intent.side} data-outcome={intent.outcome}>
      <p>
        Ticket: {intent.side} {intent.outcome}
      </p>
      <p>Ticket market: {market.status}</p>
      <p>Ticket balance: {market.viewer.balance_cr}</p>
      <button type="button" onClick={() => onIntentChange({ side: "BUY", outcome: "NO" })}>
        Ticket to buy NO
      </button>
      <button type="button" onClick={() => onIntentChange({ side: "SELL", outcome: "NO" })}>
        Ticket to sell NO
      </button>
      <button type="button" onClick={() => onIntentChange(null)}>
        Ticket done
      </button>
    </div>
  );
}

export const tradeFeature = { TradeTicket: TicketStandIn };
