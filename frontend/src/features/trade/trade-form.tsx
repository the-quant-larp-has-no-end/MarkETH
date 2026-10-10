"use client";

import { useEffect, useRef } from "react";

import { SegmentedControl } from "@/components/controls";
import { ErrorBanner, InlineError, RetryCountdown } from "@/components/feedback";
import {
  OutcomeToggle,
  QuotePanel,
  SellPresets,
  StakeLeftLine,
  StakePresets,
} from "@/components/market";
import { Amount } from "@/components/money";
import { SheetBody, SheetFooter } from "@/components/sheet";
import { Button } from "@/components/ui/button";
import { messageOf, stakeLeft, staleQuote } from "@/lib/api/errors";
import type { QuoteRequest } from "@/lib/api/keys";
import type { MarketDetailResponse, Outcome, TradeSide } from "@/lib/api/schema";
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
 * The form of the trade ticket (DESIGN.md §13), top to bottom: the question, Buy / Sell,
 * the outcome, the presets, the quote, the balance and Confirm.
 *
 * It decides nothing about amounts. Which chip can be chosen is `allowed` of the preset,
 * whether selling is offered is `allowed` of the sell presets, and whether the viewer may
 * trade is `can_trade`. A chip is sent as the string it arrived as, and the quote on
 * screen goes along as the guard.
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
  const stakePresets = attempt?.stakePresets ?? viewer.stake_presets;
  const sellPresets = attempt?.sellPresets ?? viewer.sell_presets;

  const sellable: Record<Outcome, boolean> = {
    YES: viewer.sell_presets.some((preset) => preset.outcome === "YES" && preset.allowed),
    NO: viewer.sell_presets.some((preset) => preset.outcome === "NO" && preset.allowed),
  };

  // A chip stays chosen for as long as the server allows it.
  const stake =
    stakePresets.find(
      (preset) => preset.amount_cr === choice.stake && (waiting || preset.allowed),
    ) ?? null;
  const sale =
    sellPresets.find(
      (preset) =>
        preset.outcome === outcome &&
        preset.portion === choice.portion &&
        (waiting || preset.allowed),
    ) ?? null;

  // What a quote is asked for: the chosen chip, as the string it arrived as. While a
  // trade waits, the chips are those it was made from, so this is what was sent.
  const request: QuoteRequest | null =
    side === "BUY"
      ? stake && { side, outcome, amount_cr: stake.amount_cr }
      : sale && { side, outcome, shares: sale.shares };

  const read = useQuote(market.id, request);
  // A trade that waits keeps the quote it was confirmed with on screen.
  const quote = attempt?.quote ?? read.quote;
  const quoteFailure = waiting ? null : read.error;

  const unanswered = attempt !== null && !attempt.sending ? attempt.error : null;
  const wait = useRetryAfter(unanswered);
  const refused =
    ticket.refusal !== null && ticket.refusal.choiceKey === choiceKey({ side, outcome }, choice)
      ? ticket.refusal.error
      : null;
  const stakeLimit = (refused === null ? null : stakeLeft(refused)) ?? viewer;

  // DESIGN.md §14: these two say the market on screen is not the market as it is.
  const marketBehind =
    quoteFailure?.code === "MARKET_NOT_OPEN" || quoteFailure?.code === "INSUFFICIENT_SHARES";
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
        sellPresets: viewer.sell_presets,
      });
    }
  };

  return (
    <>
      <SheetBody>
        {/* Room on the right for the close button of the sheet this may stand in. */}
        <p className="truncate pr-8 type-body-sm text-muted-foreground">{market.question}</p>

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
          <div className="flex flex-col gap-2">
            <StakePresets
              presets={stakePresets}
              value={stake?.amount_cr ?? null}
              onChange={(amount_cr) => store.choose(market.id, { stake: amount_cr })}
              disabled={waiting}
            />
            <StakeLeftLine
              stakeLeftCr={stakeLimit.stake_left_cr}
              stakeLimitCr={stakeLimit.stake_limit_cr}
            />
          </div>
        ) : (
          <SellPresets
            presets={sellPresets}
            outcome={outcome}
            value={sale?.portion ?? null}
            onChange={(preset) => store.choose(market.id, { portion: preset.portion })}
            disabled={waiting}
          />
        )}

        {/* Before a chip is chosen, and when the quote could not be read, nothing is on
            its way: the rows stand without figures instead of pulsing for ever. */}
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
          <InlineError tone={priceMoved ? "notice" : "error"}>
            {messageOf(refused)}
          </InlineError>
        )}
        {quoteFailure !== null && <ErrorBanner error={quoteFailure} onRetry={read.retry} />}

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
