"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { PauseIcon } from "lucide-react";
import { useState } from "react";

import { Banner, QueryState } from "@/components/feedback";
import {
  Deadline,
  isProposal,
  OutcomeButtons,
  Price,
  StatusBadge,
  useAskAtClose,
} from "@/components/market";
import { Amount } from "@/components/money";
import { ResponsiveSheet, sheetHeaderClasses } from "@/components/sheet";
import { NotFoundScreen, Screen, useHideTabBar } from "@/components/shell";
import { Skeleton } from "@/components/ui/skeleton";
import { TradeTicket } from "@/features/trade";
import type { ApiError } from "@/lib/api/errors";
import { keys } from "@/lib/api/keys";
import { marketHistoryQuery, marketQuery } from "@/lib/api/queries";
import type { MarketDetailResponse, MarketListResponse, MarketStatus } from "@/lib/api/schema";
import { DESKTOP_QUERY, WIDE_QUERY } from "@/lib/design";
import { isZero } from "@/lib/format";
import { useMediaQuery } from "@/lib/hooks";
import { useMarketStream } from "@/lib/live/market-stream";
import { cn } from "@/lib/utils";

import { ChartSkeleton, MarketDetailSkeleton } from "./market-detail-skeleton";
import { RejectionSection, ResolutionSection, RulesSection, VoidRuleSection } from "./market-rules";
import { PositionCard } from "./position-card";
import { ProbabilityChart } from "./probability-chart";
import { type TradeSurface, useTradeSurface } from "./trade-surface";
import { useScrolledPast } from "./use-scrolled-past";

export type MarketDetailScreenProps = {
  /** The `[id]` of the route `/markets/[id]`. */
  marketId: string;
};

/**
 * A market that was opened for trading at some point: it has a price, a chart and
 * positions. Every other status is a proposal's (in review, not approved, or expired
 * before anybody reviewed it), which the API shows to its proposer and to admins only.
 */
const isListed = (status: MarketStatus) => !isProposal(status);

/** The outcome buttons are shown, and with them the ticket (DESIGN.md §13). */
const isTrading = (status: MarketStatus) => status === "OPEN" || status === "HALTED";

/** Something can still happen to the market, so there is a stream to follow. */
const isUnsettled = (status: MarketStatus) =>
  status === "OPEN" || status === "HALTED" || status === "CLOSED";

/**
 * A market the API does not know is the "not found" screen (DESIGN.md §13). So is an id
 * that cannot be one: the API answers that with `VALIDATION_ERROR`, and it is a path the
 * app does not have.
 */
const isMissing = (error: ApiError | null) =>
  error?.code === "NOT_FOUND" || error?.code === "VALIDATION_ERROR";

/** The trade ticket beside the content: 360 px wide from 1024 px (DESIGN.md §6). */
const WITH_PANEL = "grid grid-cols-[minmax(0,1fr)_22.5rem] items-start gap-6";
const PANEL =
  "sticky top-[calc(var(--top-bar-space)+1rem)] flex max-h-[calc(100dvh-var(--top-bar-space)-2rem)] flex-col overflow-hidden rounded-lg border bg-card text-card-foreground";

/**
 * The first line of the trade ticket's surface (DESIGN.md §13): the question, on one line.
 * In the sheet it stands in the header row beside the close cross; in the panel it stands
 * alone. Either way it is above what scrolls.
 */
function TicketQuestion({ question, className }: { question: string; className?: string }) {
  return (
    <p
      data-slot="ticket-question"
      className={cn("truncate type-body-sm text-muted-foreground", className)}
    >
      {question}
    </p>
  );
}

/**
 * Whether a list of markets that was read before already holds this market as one that
 * was opened for trading. Such a market stays one, so its history can be asked for
 * together with the market instead of after it: the usual way in is a card of a list.
 */
function useSeenAsListed(marketId: string): boolean {
  const client = useQueryClient();
  const [seen] = useState(() =>
    client
      .getQueriesData<MarketListResponse>({ queryKey: keys.markets.lists })
      .some(([, list]) =>
        list?.items.some((item) => item.id === marketId && isListed(item.status)),
      ),
  );
  return seen;
}

/**
 * The one banner of the screen (DESIGN.md §13, "States by status"). The market's status
 * decides it; `OWN_MARKET` speaks only while the market is OPEN, and `MARKET_NOT_OPEN`
 * has no words of its own, because the row of the status has said it.
 */
function StatusBanner({ market }: { market: MarketDetailResponse }) {
  switch (market.status) {
    case "OPEN":
      return market.viewer.cannot_trade_reason === "OWN_MARKET" ? (
        <Banner>You proposed this market, so you cannot trade it.</Banner>
      ) : null;
    case "HALTED":
      return <Banner icon={PauseIcon}>Trading is paused.</Banner>;
    case "CLOSED":
      return (
        <Banner illustration="alarm-clock">
          Trading is over. Now we wait, like for exam results.
        </Banner>
      );
    case "VOID":
      return <Banner>This market was voided. Stakes were refunded.</Banner>;
    default:
      return null;
  }
}

/**
 * Market detail (`/markets/[id]`): DESIGN.md §13. Top to bottom: the question and its
 * badge, the Price with the volume and the deadline, the probability chart, the outcome
 * buttons, the viewer's position, the rules, the void rule and the resolution.
 *
 * The screen owns the trade ticket's surroundings. The address says which trade is asked
 * for (`?buy=YES`, `?buy=NO`, `?sell=YES`, `?sell=NO`); below 1024 px `TradeTicket` of
 * `@/features/trade` stands in a `ResponsiveSheet` that is open while it does, from
 * 1024 px in the sticky panel beside the content. The surface has the question as its
 * header, and the sheet cannot be closed while its trade is on its way.
 *
 * It is live: the Price, the chart and the volume follow the market's event stream, and
 * a `status` event reads the market again (`useMarketStream`). No event says that the
 * market's `close_at` has passed, so the screen asks again at that moment (`useAskAtClose`)
 * and shows what the answer says. Every figure is the API's, and nothing on the screen
 * says how the market is priced.
 */
export function MarketDetailScreen({ marketId }: MarketDetailScreenProps) {
  // What the screen remembers (the sheet, a payout that just arrived) belongs to one market.
  return <MarketDetail key={marketId} marketId={marketId} />;
}

function MarketDetail({ marketId }: MarketDetailScreenProps) {
  const client = useQueryClient();
  const query = useQuery(marketQuery(marketId));
  const market = query.data;
  const wide = useMediaQuery(WIDE_QUERY);
  const desktop = useMediaQuery(DESKTOP_QUERY);

  const listed = market !== undefined && isListed(market.status);
  const trading = market !== undefined && isTrading(market.status);
  const panel = desktop && trading;

  // A proposal has no history and no stream: the API answers both with NOT_FOUND. So the
  // history waits until the market says what it is, unless a list has said it before.
  const seenAsListed = useSeenAsListed(marketId);
  const history = useQuery({
    ...marketHistoryQuery(marketId),
    enabled: listed || (market === undefined && seenAsListed),
  });

  // The market resolved in front of the user: only the stream's event can say so.
  const [resolvedLive, setResolvedLive] = useState(false);
  useMarketStream(marketId, {
    enabled: market !== undefined && isUnsettled(market.status),
    onStatus: (event) => {
      if (event.status === "RESOLVED") setResolvedLive(true);
    },
  });

  // DESIGN.md §14, "Close time": while the market is shown as open for trading, the
  // market and its chart are asked for again when its close time passes. The timer asks;
  // the badge, the banner, the buttons and the deadline follow the answer.
  useAskAtClose(market?.trading_open === true ? [market.close_at] : [], () => {
    void client.invalidateQueries({ queryKey: keys.markets.detail(marketId), exact: true });
    void client.invalidateQueries({ queryKey: keys.markets.history(marketId), exact: true });
  });

  const surface = useTradeSurface(marketId, market, { desktop, panel });
  useHideTabBar(surface.sheetOpen);

  // On a phone the outcome buttons move to the bottom of the screen once the place they
  // stand in has been scrolled past.
  const [buttonsPlace, setButtonsPlace] = useState<HTMLDivElement | null>(null);
  const scrolledPast = useScrolledPast(buttonsPlace);
  const pinned = trading && !wide && scrolledPast;

  if (market === undefined && isMissing(query.error)) return <NotFoundScreen />;

  const buttons = market !== undefined && trading && (
    <OutcomeButtons
      pYes={market.p_yes}
      pNo={market.p_no}
      disabled={!market.viewer.can_trade}
      onSelect={surface.buy}
    />
  );

  // Most markets that are opened can be traded: on a wide screen the skeleton already
  // stands where their content will, beside the place of the panel.
  const beside = desktop && (market === undefined ? query.error === null : trading);

  return (
    <Screen top="inner" width={beside ? "default" : "narrow"} pinned={pinned ? buttons : null}>
      {market === undefined && <h1 className="sr-only">Market</h1>}

      <div className={cn(beside ? WITH_PANEL : "flex flex-col")}>
        <div className="flex min-w-0 flex-col gap-6">
          <QueryState query={query} skeleton={<MarketDetailSkeleton />}>
            {(data) => (
              <>
                <header className="flex flex-col gap-3">
                  <h1 className="type-h2 wrap-break-word">{data.question}</h1>
                  <StatusBadge status={data.status} outcome={data.outcome} />
                  {isListed(data.status) && (
                    <div className="flex flex-col gap-1">
                      <Price market={data} size="detail" live={isUnsettled(data.status)} />
                      <p className="flex flex-wrap items-center gap-x-3 gap-y-1 type-body-sm text-muted-foreground">
                        <span className="inline-flex items-center gap-1">
                          <Amount value={data.volume_cr} size="sm" />
                          Vol.
                        </span>
                        <Deadline closeAt={data.close_at} tradingOpen={data.trading_open} />
                      </p>
                    </div>
                  )}
                </header>

                <StatusBanner market={data} />

                {data.status === "REJECTED" && data.rejection_reason !== null && (
                  <RejectionSection reason={data.rejection_reason} />
                )}

                {isListed(data.status) && (
                  <QueryState query={history} skeleton={<ChartSkeleton />}>
                    {({ points, until }) => (
                      <ProbabilityChart points={points} until={until} market={data} />
                    )}
                  </QueryState>
                )}

                {isTrading(data.status) && (
                  // The place keeps its height while the buttons are pinned below.
                  <div ref={setButtonsPlace} data-slot="outcome-buttons-place" className="min-h-12">
                    {!pinned && buttons}
                  </div>
                )}

                {isListed(data.status) && !isZero(data.viewer.cost_cr) && (
                  <PositionCard
                    market={data}
                    trading={isTrading(data.status)}
                    payoutArrival={
                      resolvedLive &&
                      data.status === "RESOLVED" &&
                      data.viewer.payout_cr !== null &&
                      !isZero(data.viewer.payout_cr)
                        ? data.id
                        : null
                    }
                    onSell={surface.sell}
                  />
                )}

                <RulesSection market={data} />

                {isListed(data.status) && <VoidRuleSection rule={data.void_rule} />}

                {(data.status === "RESOLVED" || data.status === "VOID") &&
                  data.resolution !== null && (
                    <ResolutionSection market={data} resolution={data.resolution} />
                  )}
              </>
            )}
          </QueryState>
        </div>

        {beside &&
          (market !== undefined ? (
            <TradePanel market={market} surface={surface} />
          ) : (
            <Skeleton data-slot="trade-panel-skeleton" className="h-96 rounded-lg" />
          ))}
      </div>

      {market !== undefined && !desktop && (
        <ResponsiveSheet
          open={surface.sheetOpen}
          onOpenChange={(open) => {
            if (!open) surface.close();
          }}
          title="Trade"
          hideTitle
          header={<TicketQuestion question={market.question} />}
          dismissible={!surface.sheetLocked}
        >
          <TradeTicket
            market={market}
            intent={surface.sheetIntent}
            onIntentChange={surface.change}
          />
        </ResponsiveSheet>
      )}
    </Screen>
  );
}

/**
 * The trade ticket from 1024 px (DESIGN.md §6): a panel beside the content that stays in
 * view while the content scrolls. The ticket brings its own body and footer, so the
 * panel is the surface and its header: the question stands alone there, since a panel is
 * part of the page and has nothing to close. The ticket's body scrolls under it when the
 * window is short.
 */
function TradePanel({ market, surface }: { market: MarketDetailResponse; surface: TradeSurface }) {
  return (
    <aside aria-label="Trade" data-slot="trade-panel" className={cn(PANEL, sheetHeaderClasses)}>
      <TicketQuestion question={market.question} className="shrink-0 px-4 pt-4 pb-2" />
      <TradeTicket market={market} intent={surface.panelIntent} onIntentChange={surface.change} />
    </aside>
  );
}
