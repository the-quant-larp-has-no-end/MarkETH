"use client";

import { useEffect, useRef } from "react";

import { SegmentedControl } from "@/components/controls";
import { ErrorBanner, InlineError, RetryCountdown } from "@/components/feedback";
import { OutcomeToggle, QuotePanel, SharesEntry } from "@/components/market";
import { Amount } from "@/components/money";
import { SheetBody, SheetFooter } from "@/components/sheet";
import { Button } from "@/components/ui/button";
import { type ApiError, messageOf, placeError, stakeLeft, staleQuote } from "@/lib/api/errors";
import type { QuoteRequest } from "@/lib/api/keys";
import type { ErrorCode, MarketDetailResponse, Outcome, TradeSide } from "@/lib/api/schema";
import { useRetryAfter } from "@/lib/hooks";

import { confirmLabel, otherOutcome, type TradeIntent } from "./intent";
import { choiceKey, type TicketState, type TicketStore } from "./ticket-store";
import { useQuote } from "./use-quote";

export type TradeFormProps = {
  market: MarketDetailResponse;
  /** What the owner set the ticket to. A trade that waits for its verdict overrules it. */
  intent: TradeIntent;
  ticket: TicketState;
  store: TicketStore;
  onIntentChange: (intent: TradeIntent) => void;
  /** An answer said the market on screen is behind: ask for it again. */
  onMarketBehind: () => void;
};

/**
 * What a quote says about the size that was entered (DESIGN.md §13). Of a buy: the stake
 * limit, the balance, the smallest trade, or a text that is no number of shares. Of a sell: more
 * shares than the viewer holds, the smallest trade, or a text that is no number of shares.
 * It is said with the entry, which is where the size is.
 */
const ABOUT_THE_SIZE: Record<TradeSide, readonly ErrorCode[]> = {
  BUY: ["LIMIT_EXCEEDED", "INSUFFICIENT_BALANCE", "TRADE_TOO_SMALL", "VALIDATION_ERROR"],
  SELL: ["INSUFFICIENT_SHARES", "TRADE_TOO_SMALL", "VALIDATION_ERROR"],
};

/**
 * What a quote says about the viewer and the market: trading ended, the holding changed,
 * or the viewer proposed the market. The market on screen is then behind the market as it
 * is, so it is read again, and the message stands above the button as a trade's does.
 * What a sale says about the holding stands with the shares entry instead.
 */
const ABOUT_THE_MARKET: readonly ErrorCode[] = [
  "MARKET_NOT_OPEN",
  "INSUFFICIENT_SHARES",
  "FORBIDDEN",
];

/** Where a quote that could not be had is said: with the entry, above the button, or as the banner. */
function placeOfFailure(error: ApiError, side: TradeSide): "entry" | "button" | "banner" {
  if (error.code === null) return "banner";
  if (ABOUT_THE_SIZE[side].includes(error.code)) return "entry";
  return ABOUT_THE_MARKET.includes(error.code) ? "button" : "banner";
}

/**
 * The API's sentence for `shares`, or its message when no field is named (DESIGN.md §14).
 */
function aboutTheSize(error: ApiError): string {
  return placeError(error, ["shares"]).fields.shares ?? messageOf(error);
}

/**
 * The form of the trade ticket (DESIGN.md §13), top to bottom: Buy / Sell, the outcome,
 * the shares entry, the quote, the balance and Confirm. The question
 * stands above it, in the header of the surface the ticket is on.
 *
 * The size of a buy or sale is the text of its shares field, sent as it stands; whether it
 * can be traded is the server's answer to the quote. Which preset can be chosen is its
 * `allowed`, whether selling is offered is `allowed` of the sell presets, whether the
 * viewer may trade is `can_trade`, and whether anything can be bought is `can_buy`: while
 * it is false, at the stake limit or without the balance of the smallest buy, the shares
 * field takes nothing and no quote is asked. The quote on screen goes along as the
 * guard of the trade.
 *
 * While a trade waits for its verdict (DESIGN.md §14) the form shows that trade as it was
 * sent, with everything disabled, and offers nothing but "Try again".
 */
export function TradeForm({
  market,
  intent,
  ticket,
  store,
  onIntentChange,
  onMarketBehind,
}: TradeFormProps) {
  const { viewer } = market;
  const { attempt, choice } = ticket;

  const waiting = attempt !== null;
  const { side, outcome } = attempt?.intent ?? intent;
  // While a trade waits, the controls are those it was made from.
  const buy = (attempt?.buy ?? viewer.buy).find((entry) => entry.outcome === outcome);
  const sellPresets = attempt?.sellPresets ?? viewer.sell_presets;
  const sellSlider =
    (attempt?.sell ?? viewer.sell).find((entry) => entry.outcome === outcome)?.slider ?? null;
  const shares =
    attempt && "shares" in attempt.request
      ? attempt.request.shares
      : side === "BUY"
        ? choice.buyShares
        : choice.shares;

  const sellable: Record<Outcome, boolean> = {
    YES: viewer.sell_presets.some((preset) => preset.outcome === "YES" && preset.allowed),
    NO: viewer.sell_presets.some((preset) => preset.outcome === "NO" && preset.allowed),
  };

  // What a quote is asked for: the text of the field of the side, as it stands. An empty
  // field asks for nothing, and neither does one that ends in its decimal point: that
  // figure is not finished.
  const finished = shares !== "" && !shares.endsWith(".");
  // The server says that nothing can be bought right now.
  const nothingToBuy = side === "BUY" && !viewer.can_buy;
  const entered: QuoteRequest | null = !finished || nothingToBuy ? null : { side, outcome, shares };
  // A trade that waits was made from a request: that one is what was sent.
  const request = attempt?.request ?? entered;

  const read = useQuote(market.id, request, viewer.can_trade && !waiting);
  // A trade that waits keeps the quote it was confirmed with on screen. Otherwise a quote
  // counts for as long as the server lets the viewer trade.
  const quote = attempt?.quote ?? (viewer.can_trade ? read.quote : null);

  const unanswered = attempt !== null && !attempt.sending ? attempt.error : null;
  const wait = useRetryAfter(unanswered);
  const refused =
    ticket.refusal !== null && ticket.refusal.choiceKey === choiceKey({ side, outcome }, choice)
      ? ticket.refusal.error
      : null;

  // Why there is no quote. While the refusal of a trade stands above the button, a quote
  // that gives the same verdict for the same input says nothing more, and is not said twice.
  const said = refused !== null && read.error?.code === refused.code;
  const quoteFailure = waiting || said ? null : read.error;
  const place = quoteFailure === null ? null : placeOfFailure(quoteFailure, side);
  const sizeRefused = place === "entry" ? quoteFailure : null;
  // A `LIMIT_EXCEEDED`, of the trade or of the quote, says what is left as of its answer.
  const stakeLimit =
    (refused === null ? null : stakeLeft(refused)) ??
    (sizeRefused === null ? null : stakeLeft(sizeRefused)) ??
    viewer;

  // DESIGN.md §14: such an answer says the market on screen is not the market as it is.
  // So does a sale of more shares than the viewer holds: the holding, and with it the
  // slider and the chips, is read again.
  const marketBehind =
    place === "button" ||
    (quoteFailure !== null &&
      ["INSUFFICIENT_SHARES", "INSUFFICIENT_BALANCE", "LIMIT_EXCEEDED"].includes(
        quoteFailure.code ?? "",
      ));
  useEffect(() => {
    if (marketBehind) onMarketBehind();
  }, [marketBehind, quoteFailure, onMarketBehind]);

  // DESIGN.md §13: after `QUOTE_STALE` the panel shows another quote and the notice asks
  // the user to review it. A short screen shows the button without the panel, so the
  // panel is brought into view.
  const priceMoved = refused !== null && staleQuote(refused) !== null;
  const quotePlace = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (priceMoved) quotePlace.current?.scrollIntoView({ block: "nearest" });
  }, [priceMoved, refused]);

  const changeSide = (next: TradeSide) => {
    store.choose(market.id, { shares: "" });
    // Selling starts on an outcome that can be sold.
    const sold = sellable[outcome] ? outcome : otherOutcome(outcome);
    onIntentChange({ side: next, outcome: next === "SELL" ? sold : outcome });
  };

  const changeOutcome = (next: Outcome) => {
    // A number of shares is of one holding: the other outcome starts on an empty field.
    store.choose(market.id, { shares: "" });
    onIntentChange({ side, outcome: next });
  };

  const act = () => {
    if (waiting) {
      store.retry(market.id);
    } else if (request !== null && quote !== null) {
      store.confirm(market.id, {
        intent: { side, outcome },
        request,
        quote,
        buy: viewer.buy,
        sellPresets: viewer.sell_presets,
        sell: viewer.sell,
      });
    }
  };

  return (
    <>
      <SheetBody>
        <SegmentedControl
          label="Buy or sell"
          value={side}
          onChange={changeSide}
          disabled={waiting}
          segments={[
            { value: "BUY", label: "Buy" },
            { value: "SELL", label: "Sell", disabled: !sellable.YES && !sellable.NO },
          ]}
        />

        <OutcomeToggle
          value={outcome}
          onChange={changeOutcome}
          disabled={waiting || (side === "SELL" && { YES: !sellable.YES, NO: !sellable.NO })}
        />

        {side === "BUY" ? (
          <SharesEntry
            side="BUY"
            value={shares}
            onChange={(text) => store.choose(market.id, { buyShares: text })}
            slider={buy?.slider ?? null}
            presets={buy?.presets ?? []}
            stakeLeftCr={stakeLimit.stake_left_cr}
            stakeLimitCr={stakeLimit.stake_limit_cr}
            refusal={sizeRefused === null ? null : aboutTheSize(sizeRefused)}
            disabled={waiting || !viewer.can_trade || !viewer.can_buy}
          />
        ) : (
          <SharesEntry
            side="SELL"
            value={shares}
            onChange={(text) => store.choose(market.id, { shares: text })}
            outcome={outcome}
            slider={sellSlider}
            presets={sellPresets}
            refusal={sizeRefused === null ? null : aboutTheSize(sizeRefused)}
            disabled={waiting || !viewer.can_trade}
          />
        )}

        {/* With nothing to quote, nothing is on its way: the rows stand without figures
            instead of pulsing for ever. */}
        <div ref={quotePlace}>
          <QuotePanel
            side={side}
            outcome={outcome}
            quote={quote}
            idle={quote === null && !read.loading}
          />
        </div>

        <p className="flex items-center justify-between gap-3">
          <span className="type-body-sm text-muted-foreground">Balance</span>
          <Amount value={viewer.balance_cr} />
        </p>
      </SheetBody>

      <SheetFooter>
        {unanswered !== null && (
          <div className="flex flex-col gap-1">
            <InlineError>{messageOf(unanswered)}</InlineError>
            <RetryCountdown error={unanswered} />
          </div>
        )}
        {refused !== null && (
          // The price moved: a notice, not a fault of the user's. It is announced the same.
          <InlineError tone={priceMoved ? "notice" : "error"}>{messageOf(refused)}</InlineError>
        )}
        {quoteFailure !== null && place === "button" && (
          <InlineError>{messageOf(quoteFailure)}</InlineError>
        )}
        {quoteFailure !== null && place === "banner" && (
          <ErrorBanner error={quoteFailure} onRetry={read.retry} />
        )}

        <Button
          size="confirm"
          width="full"
          variant={side === "SELL" ? "primary" : outcome === "YES" ? "yes" : "no"}
          loading={attempt?.sending === true}
          disabled={waiting ? wait > 0 : !viewer.can_trade || request === null || quote === null}
          onClick={act}
        >
          {waiting && attempt.error !== null ? "Try again" : confirmLabel({ side, outcome })}
        </Button>
      </SheetFooter>
    </>
  );
}
