"use client";

import type { QueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { type ApiError, staleQuote } from "@/lib/api/errors";
import { keys, type QuoteRequest } from "@/lib/api/keys";
import { tradeMutation } from "@/lib/api/mutations";
import type {
  BuyEntry,
  ErrorCode,
  QuoteResponse,
  SellEntry,
  SellPreset,
  TradeRequest,
  TradeResponse,
} from "@/lib/api/schema";
import {
  type Attempt as KeptAttempt,
  type SubmissionRules,
  type SubmissionState,
  submissionStore,
} from "@/lib/api/submissions";

import { receiptHeadline, type TradeIntent } from "./intent";

/**
 * What a trade ticket holds, one state per market, in memory and outside the component
 * (DESIGN.md §14, "A submitted trade").
 *
 * Confirm serialises the request body once, makes one Idempotency-Key for it and keeps
 * both as an `Attempt`. They stay until an answer says what became of that request: a 2xx
 * (the trade exists) or a 4xx other than 429 (the trade was refused and nothing was
 * booked). Any 5xx, a 429, the portal's 401 and a request without a response say nothing
 * about the trade: the attempt stays, and "Try again" sends the stored body with the
 * stored key, unchanged. A body built a second time, from a later quote, would be another
 * request and could trade twice. That rule is `lib/api/submissions.ts`, which the
 * adjustment of a user's tokens follows too; this file adds what a ticket holds besides:
 * what is entered, a refusal, a receipt.
 *
 * It is not component state because the ticket goes away while a trade may still be open:
 * closing the sheet unmounts it, and so does turning a phone, when the sheet becomes a
 * dialog. Whatever is here is still here when the ticket comes back. Nothing is written
 * to browser storage, so a reload forgets it, and the market, the balance and the History
 * then show what the server recorded.
 */

/** The shares fields of a buy and a sale, kept separately (DESIGN.md §13). */
export type Choice = {
  /**
   * The text of the buy shares field: what was typed, the `shares` of a shortcut or the
   * digits of a step of the slider. Empty until something is entered. It stays the string
   * it is, and is sent as it stands.
   */
  readonly buyShares: string;
  /**
   * The text of the shares field of a sell: what was typed, the `shares` of a sell preset
   * or the digits of a step of the slider. Empty until something is entered, and sent as
   * it stands.
   */
  readonly shares: string;
};

/** What Confirm sends, with what the ticket showed when it was tapped. */
export type Order = {
  readonly intent: TradeIntent;
  /**
   * What the quote was asked for. The ticket sends the shares field's text as `shares`.
   */
  readonly request: QuoteRequest;
  /** The quote on screen. It is the guard of the trade. */
  readonly quote: QuoteResponse;
  /** The controls as they stood, so that a trade that waits is shown as it was sent. */
  readonly buy: readonly BuyEntry[];
  readonly sellPresets: readonly SellPreset[];
  readonly sell: readonly SellEntry[];
};

/** What a ticket shows of a trade while it waits: the order, and the choice it was made from. */
type Sent = Order & {
  /** The choice the trade was made from (`choiceKey`). */
  readonly choiceKey: string;
};

/**
 * A trade that went out and has no verdict yet: what was sent, the body and its key as
 * `submission`, `sending` while a request is on its way, and `error` once one brought no
 * verdict.
 */
export type Attempt = KeptAttempt<Sent>;

/** The verdict that a trade was refused. It is about what was sent. */
export type Refusal = {
  /** The choice the refused trade was made from: the refusal holds while that choice stands. */
  readonly choiceKey: string;
  readonly error: ApiError;
};

/** What a ticket holds beside the trade that waits. */
type Rest = {
  readonly choice: Choice;
  readonly refusal: Refusal | null;
  /** The executed trade, until "Done", or until the sheet that shows it is gone. */
  readonly receipt: TradeResponse | null;
};

/**
 * The ticket of one market: `choice`, `refusal`, `receipt`, and `attempt`, which is set
 * from Confirm until a 2xx or a 4xx other than 429. While it is set, nothing else is
 * offered.
 */
export type TicketState = SubmissionState<Sent, Rest>;

const UNTOUCHED: Rest = {
  choice: { buyShares: "", shares: "" },
  refusal: null,
  receipt: null,
};

/**
 * Names a choice: the side, the outcome and what is entered for that side. A refusal
 * belongs to one of these.
 */
export function choiceKey(intent: TradeIntent, choice: Choice): string {
  const entered = intent.side === "BUY" ? choice.buyShares : choice.shares;
  return [intent.side, intent.outcome, entered].join(":");
}

/**
 * The body of a trade (DESIGN.md §13): what was quoted, and the quote on screen as its
 * guard. Buying by an amount sends the text of the amount field and is guarded by the
 * quote's `shares`; buying or selling by shares sends the text of the shares field and is
 * guarded by the quote's `amount_cr`. A price
 * that moved against the user is therefore always answered `QUOTE_STALE`. Every amount is
 * a string that was typed or that the API sent, and none is changed on its way.
 */
function tradeRequest(request: QuoteRequest, quote: QuoteResponse): TradeRequest {
  if ("amount_cr" in request) {
    return {
      side: "BUY",
      outcome: request.outcome,
      amount_cr: request.amount_cr,
      min_shares: quote.shares,
      quote_version: quote.quote_version,
    };
  }
  if (request.side === "BUY") {
    return {
      side: "BUY",
      outcome: request.outcome,
      shares: request.shares,
      max_cost_cr: quote.amount_cr,
      quote_version: quote.quote_version,
    };
  }
  return {
    side: "SELL",
    outcome: request.outcome,
    shares: request.shares,
    min_proceeds_cr: quote.amount_cr,
    quote_version: quote.quote_version,
  };
}

/**
 * The refusals that say the screen is behind: the balance, the stake, the holding or the
 * market's status is another one than the ticket showed. The trade's own handling reads
 * the market again for them (`tradeMutation`). The quote on screen is about the same
 * caller and the same market, so it did not hold either.
 */
const SCREEN_BEHIND: readonly ErrorCode[] = [
  "INSUFFICIENT_BALANCE",
  "INSUFFICIENT_SHARES",
  "MARKET_NOT_OPEN",
  "LIMIT_EXCEEDED",
];

/** What the two verdicts on a trade do to its ticket. */
const TRADES: SubmissionRules<TradeRequest, TradeResponse, Sent, Rest> = {
  mutation: tradeMutation,
  untouched: UNTOUCHED,

  /** A 2xx: the trade exists. */
  answered({ answer, onScreen }) {
    if (onScreen) return { ...UNTOUCHED, receipt: answer };
    // The sheet was closed while the trade was on its way: nobody sees a receipt, so the
    // finished action is confirmed with a toast, and the next opening finds an empty form.
    toast(receiptHeadline(answer));
    return UNTOUCHED;
  },

  /**
   * A 4xx other than 429: refused, nothing booked. After QUOTE_STALE the quote of the
   * answer is the quote for this input. After a refusal that says the screen is behind,
   * the quote for this input is asked for again: the server judges the amount, and Confirm
   * waits for what it says now. Either way only a quote the sheet is showing is touched.
   */
  refused({ target: marketId, rest, attempt, error, client }) {
    const moved = staleQuote(error);
    const quoteKey = keys.markets.quote(marketId, attempt.request);
    if (client.getQueryState(quoteKey) !== undefined) {
      if (moved !== null) {
        client.setQueryData<QuoteResponse>(quoteKey, moved);
      } else if (error.code !== null && SCREEN_BEHIND.includes(error.code)) {
        void client.invalidateQueries({ queryKey: quoteKey, exact: true });
      }
    }
    return { ...rest, refusal: { choiceKey: attempt.choiceKey, error } };
  },
};

export type TicketStore = {
  /** The ticket of one market. The same object until something changes. */
  read(marketId: string): TicketState;
  /** Follows one market's ticket while it is on screen. Returns how to stop. */
  watch(marketId: string, onChange: () => void): () => void;
  /**
   * The user changed the form: the amount, the shares, the side or the outcome. A refusal was
   * about what was sent, so it goes. Does nothing while a trade waits or a receipt is
   * shown.
   */
  choose(marketId: string, choice?: Partial<Choice>): void;
  /** Confirm: one body, one key, one request. A second tap while a trade waits does nothing. */
  confirm(marketId: string, order: Order): void;
  /** "Try again": the stored body with the stored key. Does nothing while one is on its way. */
  retry(marketId: string): void;
  /** "Done" on the receipt: the ticket is back on an empty form. */
  done(marketId: string): void;
};

function createTicketStore(client: QueryClient): TicketStore {
  // The one caller of `tradeMutation`: the store that keeps a body and its key.
  const trades = submissionStore(client, TRADES);

  return {
    read: trades.read,
    watch: trades.watch,

    choose(marketId, choice = {}) {
      if (trades.read(marketId).receipt !== null) return;
      trades.change(marketId, (ticket) => ({
        ...ticket,
        choice: { ...ticket.choice, ...choice },
        refusal: null,
      }));
    },

    confirm(marketId, order) {
      const ticket = trades.read(marketId);
      if (ticket.receipt !== null) return;
      trades.submit(
        marketId,
        tradeRequest(order.request, order.quote),
        { ...order, choiceKey: choiceKey(order.intent, ticket.choice) },
        { rest: (held) => ({ ...held, refusal: null }) },
      );
    },

    retry(marketId) {
      trades.retry(marketId);
    },

    done(marketId) {
      trades.change(marketId, () => UNTOUCHED);
    },
  };
}

const stores = new WeakMap<QueryClient, TicketStore>();

/**
 * The tickets that belong to one query client. The app has one client for as long as the
 * page lives, so this is "in memory": it survives every sheet and is gone with a reload.
 */
export function ticketStore(client: QueryClient): TicketStore {
  let store = stores.get(client);
  if (store === undefined) {
    store = createTicketStore(client);
    stores.set(client, store);
  }
  return store;
}
