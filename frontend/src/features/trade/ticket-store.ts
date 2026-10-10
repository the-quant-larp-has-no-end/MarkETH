"use client";

import { MutationObserver, type QueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import {
  ApiError,
  GENERIC_MESSAGE,
  isApiError,
  keepsTradeRequest,
  staleQuote,
} from "@/lib/api/errors";
import { keys, type QuoteRequest } from "@/lib/api/keys";
import { newTradeSubmission, tradeMutation, type TradeSubmission } from "@/lib/api/mutations";
import type {
  QuoteResponse,
  SellPortion,
  SellPreset,
  StakePreset,
  TradeRequest,
  TradeResponse,
} from "@/lib/api/schema";

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
 * request and could trade twice.
 *
 * It is not component state because the ticket goes away while a trade may still be open:
 * closing the sheet unmounts it, and so does turning a phone, when the sheet becomes a
 * dialog. Whatever is here is still here when the ticket comes back. Nothing is written
 * to browser storage, so a reload forgets it, and the market, the balance and the History
 * then show what the server recorded.
 */

/** The chips that are chosen: a stake when buying, a part of the holding when selling. */
export type Choice = {
  /** `amount_cr` of the chosen stake preset, as it arrived. */
  readonly stake: string | null;
  /** `portion` of the chosen sell preset. Its `shares` are read from the market. */
  readonly portion: SellPortion | null;
};

/** What Confirm sends, with what the ticket showed when it was tapped. */
export type Order = {
  readonly intent: TradeIntent;
  /** What the quote was asked for: the preset's `amount_cr` or `shares`, as it arrived. */
  readonly request: QuoteRequest;
  /** The quote on screen. It is the guard of the trade. */
  readonly quote: QuoteResponse;
  /** The chips as they stood, so that a trade that waits is shown as it was sent. */
  readonly stakePresets: readonly StakePreset[];
  readonly sellPresets: readonly SellPreset[];
};

/** A trade that went out and has no verdict yet. */
export type Attempt = Order & {
  /** The body and its key, exactly as they went out. Never built again, never re-keyed. */
  readonly submission: TradeSubmission;
  /** The choice it was made from (`choiceKey`). */
  readonly choiceKey: string;
  /** True while a request is on its way. */
  readonly sending: boolean;
  /** Why the latest request brought no verdict. Null while the first one is on its way. */
  readonly error: ApiError | null;
};

/** The verdict that a trade was refused. It is about what was sent. */
export type Refusal = {
  /** The choice the refused trade was made from: once that changes, this no longer applies. */
  readonly choiceKey: string;
  readonly error: ApiError;
};

export type TicketState = {
  readonly choice: Choice;
  /** Set from Confirm until a 2xx or a 4xx other than 429. While set, nothing else is offered. */
  readonly attempt: Attempt | null;
  readonly refusal: Refusal | null;
  /** The executed trade, until "Done", or until the sheet that shows it is gone. */
  readonly receipt: TradeResponse | null;
};

const UNTOUCHED: TicketState = {
  choice: { stake: null, portion: null },
  attempt: null,
  refusal: null,
  receipt: null,
};

/** Names a choice: the side, the outcome and the chip. A refusal belongs to one of these. */
export function choiceKey(intent: TradeIntent, choice: Choice): string {
  const chip = intent.side === "BUY" ? choice.stake : choice.portion;
  return [intent.side, intent.outcome, chip ?? ""].join(":");
}

/**
 * The body of a trade (DESIGN.md §13): the chip as it arrived, and the quote on screen as
 * its guard. Buying is guarded by the quote's `shares`, selling by its `amount_cr`, so a
 * price that moved against the user is always answered `QUOTE_STALE`. Every amount is the
 * string the API sent.
 */
function tradeRequest(request: QuoteRequest, quote: QuoteResponse): TradeRequest {
  if (request.side === "BUY") {
    return {
      side: "BUY",
      outcome: request.outcome,
      amount_cr: request.amount_cr,
      min_shares: quote.shares,
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
 * What went wrong, as an `ApiError`. `call()` throws nothing else, so anything else is a
 * fault in handling an answer. Nobody can then say what became of the trade, which is the
 * case of a request without a response: the attempt is kept.
 */
function asApiError(failure: unknown): ApiError {
  if (isApiError(failure)) return failure;
  return new ApiError({
    kind: "network",
    status: 0,
    code: null,
    message: GENERIC_MESSAGE,
    cause: failure,
  });
}

export type TicketStore = {
  /** The ticket of one market. The same object until something changes. */
  read(marketId: string): TicketState;
  /** Follows one market's ticket while it is on screen. Returns how to stop. */
  watch(marketId: string, onChange: () => void): () => void;
  /**
   * The user changed the form: a chip, the side or the outcome. A refusal was about what
   * was sent, so it goes. Does nothing while a trade waits or a receipt is shown.
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
  const tickets = new Map<string, TicketState>();
  const watchers = new Map<string, Set<() => void>>();

  // The one caller of `tradeMutation`, with the cache handling it brings. Like every write
  // of the app a trade goes out at once or fails at once (`createQueryClient`): nothing
  // of it is held back to be sent later, without a tap.
  const sender = new MutationObserver(client, tradeMutation);

  const read = (marketId: string): TicketState => tickets.get(marketId) ?? UNTOUCHED;

  const write = (marketId: string, next: TicketState): void => {
    if (next === UNTOUCHED) tickets.delete(marketId);
    else tickets.set(marketId, next);
    for (const onChange of [...(watchers.get(marketId) ?? [])]) onChange();
  };

  const onScreen = (marketId: string): boolean => (watchers.get(marketId)?.size ?? 0) > 0;

  /** A 2xx: the trade exists. The body and its key have done their work. */
  const executed = (marketId: string, submission: TradeSubmission, trade: TradeResponse): void => {
    if (read(marketId).attempt?.submission !== submission) return;
    if (onScreen(marketId)) {
      write(marketId, { ...UNTOUCHED, receipt: trade });
      return;
    }
    // The sheet was closed while the trade was on its way: nobody sees a receipt, so the
    // finished action is confirmed with a toast, and the next opening finds an empty form.
    write(marketId, UNTOUCHED);
    toast(receiptHeadline(trade));
  };

  const failed = (marketId: string, submission: TradeSubmission, error: ApiError): void => {
    const ticket = read(marketId);
    const { attempt } = ticket;
    if (attempt?.submission !== submission) return;

    if (keepsTradeRequest(error)) {
      // No verdict. The same body and the same key wait for "Try again".
      write(marketId, { ...ticket, attempt: { ...attempt, sending: false, error } });
      return;
    }

    // A verdict: refused, nothing booked. The next Confirm builds another body with
    // another key. After QUOTE_STALE the quote of the answer is the quote for this input.
    const moved = staleQuote(error);
    const quoteKey = keys.markets.quote(marketId, attempt.request);
    if (moved !== null && client.getQueryState(quoteKey) !== undefined) {
      client.setQueryData<QuoteResponse>(quoteKey, moved);
    }
    write(marketId, {
      ...ticket,
      attempt: null,
      refusal: { choiceKey: attempt.choiceKey, error },
    });
  };

  const send = (marketId: string, submission: TradeSubmission): void => {
    sender.mutate(submission).then(
      (trade) => executed(marketId, submission, trade),
      (failure: unknown) => failed(marketId, submission, asApiError(failure)),
    );
  };

  /** The ticket left the screen for good: only a trade without a verdict outlives that. */
  const leave = (marketId: string): void => {
    if (read(marketId).attempt === null) tickets.delete(marketId);
  };

  return {
    read,

    watch(marketId, onChange) {
      let listening = watchers.get(marketId);
      if (listening === undefined) {
        listening = new Set();
        watchers.set(marketId, listening);
      }
      const set = listening;
      set.add(onChange);

      return () => {
        set.delete(onChange);
        if (set.size > 0) return;
        // Closed, or mounted afresh in the same commit (the sheet turning into a dialog)?
        // Look once that commit is over: a ticket that is back keeps what it showed.
        setTimeout(() => {
          if (set.size === 0) leave(marketId);
        }, 0);
      };
    },

    choose(marketId, choice = {}) {
      const ticket = read(marketId);
      if (ticket.attempt !== null || ticket.receipt !== null) return;
      write(marketId, { ...ticket, choice: { ...ticket.choice, ...choice }, refusal: null });
    },

    confirm(marketId, order) {
      const ticket = read(marketId);
      if (ticket.attempt !== null || ticket.receipt !== null) return;

      const submission = newTradeSubmission(marketId, tradeRequest(order.request, order.quote));
      write(marketId, {
        ...ticket,
        refusal: null,
        attempt: {
          ...order,
          submission,
          choiceKey: choiceKey(order.intent, ticket.choice),
          sending: true,
          error: null,
        },
      });
      send(marketId, submission);
    },

    retry(marketId) {
      const ticket = read(marketId);
      const { attempt } = ticket;
      if (attempt === null || attempt.sending) return;

      write(marketId, { ...ticket, attempt: { ...attempt, sending: true } });
      send(marketId, attempt.submission);
    },

    done(marketId) {
      if (read(marketId).attempt !== null) return;
      write(marketId, UNTOUCHED);
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
