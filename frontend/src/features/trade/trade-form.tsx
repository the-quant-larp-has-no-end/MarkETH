"use client";

import { useEffect, useRef } from "react";

import { SegmentedControl } from "@/components/controls";
import { ErrorBanner, InlineError, RetryCountdown } from "@/components/feedback";
import { OutcomeToggle, QuotePanel, SellPresets, StakeEntry } from "@/components/market";
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
 * What the quote of a buy says about the amount (DESIGN.md §13): the stake limit, the
 * balance, the smallest trade, or a text that is no amount. It is said with the stake
 * entry, which is where the amount is.
 */
const ABOUT_THE_AMOUNT: readonly ErrorCode[] = [
  "LIMIT_EXCEEDED",
  "INSUFFICIENT_BALANCE",
  "TRADE_TOO_SMALL",
  "VALIDATION_ERROR",
];

/**
 * What a quote says about the viewer and the market: trading ended, the holding changed,
 * or the viewer proposed the market. The market on screen is then behind the market as it
 * is, so it is read again, and the message stands above the button as a trade's does.
 */
const ABOUT_THE_MARKET: readonly ErrorCode[] = [
  "MARKET_NOT_OPEN",
  "INSUFFICIENT_SHARES",
  "FORBIDDEN",
];

/** Where a quote that could not be had is said: with the stake, above the button, or as the banner. */
function placeOfFailure(error: ApiError, side: TradeSide): "stake" | "button" | "banner" {
  if (error.code === null) return "banner";
  if (side === "BUY" && ABOUT_THE_AMOUNT.includes(error.code)) return "stake";
  return ABOUT_THE_MARKET.includes(error.code) ? "button" : "banner";
}

/**
 * The sentence the API has for the amount: the one it wrote for the field `amount_cr`
 * where it names that field, otherwise its `message` (DESIGN.md §14).
 */
function aboutTheAmount(error: ApiError): string {
  return placeError(error, ["amount_cr"]).fields.amount_cr ?? messageOf(error);
}

/**
 * The form of the trade ticket (DESIGN.md §13), top to bottom: Buy / Sell, the outcome,
 * the stake entry or the sell presets, the quote, the balance and Confirm. The question
 * stands above it, in the header of the surface the ticket is on.
 *
 * It decides nothing about amounts. The amount of a buy is the text of the amount field,
 * sent as it stands; whether it can be traded is the server's answer to the quote. Which
 * preset can be chosen is its `allowed`, whether selling is offered is `allowed` of the
 * sell presets, and whether the viewer may trade is `can_trade`. The quote on screen goes
 * along as the guard of the trade.
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
  const stakePresets = attempt?.stakePresets ?? viewer.stake_presets;
  const buySlider = attempt === null ? viewer.buy_slider : attempt.buySlider;
  const sellPresets = attempt?.sellPresets ?? viewer.sell_presets;
  const stake = attempt?.request.side === "BUY" ? attempt.request.amount_cr : choice.stake;

  const sellable: Record<Outcome, boolean> = {
    YES: viewer.sell_presets.some((preset) => preset.outcome === "YES" && preset.allowed),
    NO: viewer.sell_presets.some((preset) => preset.outcome === "NO" && preset.allowed),
  };

  // A sell preset stays chosen for as long as the server allows it.
  const sale =
    sellPresets.find(
      (preset) =>
        preset.outcome === outcome &&
        preset.portion === choice.portion &&
        (waiting || preset.allowed),
    ) ?? null;

  // What a quote is asked for. Buying: the text of the amount field, as it stands. An
  // empty field asks for nothing, and neither does one that ends in its decimal point:
  // that amount is not finished. Selling: the chosen preset's shares, as they arrived.
  const finished = stake !== "" && !stake.endsWith(".");
  const entered: QuoteRequest | null =
    side === "BUY"
      ? finished
        ? { side, outcome, amount_cr: stake }
        : null
      : sale && { side, outcome, shares: sale.shares };
  // A trade that waits was made from a request: that one is what was sent.
  const request = attempt?.request ?? entered;

  const read = useQuote(market.id, request);
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
  const amountRefused = place === "stake" ? quoteFailure : null;
  // A `LIMIT_EXCEEDED`, of the trade or of the quote, says what is left as of its answer.
  const stakeLimit =
    (refused === null ? null : stakeLeft(refused)) ??
    (amountRefused === null ? null : stakeLeft(amountRefused)) ??
    viewer;

  // DESIGN.md §14: such an answer says the market on screen is not the market as it is.
  const marketBehind = place === "button";
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
    store.choose(market.id, { portion: null });
    // Selling starts on an outcome that can be sold.
    const sold = sellable[outcome] ? outcome : otherOutcome(outcome);
    onIntentChange({ side: next, outcome: next === "SELL" ? sold : outcome });
  };

  const changeOutcome = (next: Outcome) => {
    store.choose(market.id, { portion: null });
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
        stakePresets: viewer.stake_presets,
        buySlider: viewer.buy_slider,
        sellPresets: viewer.sell_presets,
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
          <StakeEntry
            value={stake}
            onChange={(text) => store.choose(market.id, { stake: text })}
            slider={buySlider}
            presets={stakePresets}
            stakeLeftCr={stakeLimit.stake_left_cr}
            stakeLimitCr={stakeLimit.stake_limit_cr}
            refusal={amountRefused === null ? null : aboutTheAmount(amountRefused)}
            disabled={waiting || !viewer.can_trade}
          />
        ) : (
          <SellPresets
            presets={sellPresets}
            outcome={outcome}
            value={sale?.portion ?? null}
            onChange={(preset) => store.choose(market.id, { portion: preset.portion })}
            disabled={waiting}
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
