"use client";

import { useQuery } from "@tanstack/react-query";
import { InfoIcon, type LucideIcon, OctagonAlertIcon, TriangleAlertIcon } from "lucide-react";
import { type ReactNode, useId } from "react";

import {
  Banner,
  type BannerProps,
  InlineError,
  QueryState,
  RetryCountdown,
} from "@/components/feedback";
import { StatusBadge, tierName } from "@/components/market";
import { Amount, Missing } from "@/components/money";
import { Screen } from "@/components/shell";
import { DateTime } from "@/components/time";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { messageOf } from "@/lib/api/errors";
import { acknowledgeAlertMutation } from "@/lib/api/mutations";
import { riskQuery } from "@/lib/api/queries";
import type { AlertItem, AlertSeverity, RiskMarketRow, RiskResponse } from "@/lib/api/schema";
import { useConfig } from "@/lib/api/session";
import { useSubmit } from "@/lib/api/submit";
import { formatAmount, formatCount, formatDate, formatTime } from "@/lib/format";
import { useRetryAfter } from "@/lib/hooks";
import { cn } from "@/lib/utils";

import { AdminTabs } from "./admin-tabs";
import { Chance, type Column, DataTable, DataTableSkeleton, EmptyLine } from "./data-table";

/* ---- Tiles ------------------------------------------------------------------------ */

/** One figure of the risk page with its label, and beside it what the API sends with it. */
function Tile({
  label,
  figure,
  beside,
  alert = false,
  className,
  children,
}: {
  label: string;
  figure: ReactNode;
  /** The figure's limit, side by side with it: "of" and the limit. */
  beside?: ReactNode;
  /** The API's own flag for this figure: an outline in `--no` and an alert icon. */
  alert?: boolean;
  className?: string;
  children?: ReactNode;
}) {
  const id = useId();
  return (
    <Card
      data-slot="tile"
      role="group"
      aria-labelledby={id}
      className={cn(alert && "border-no", className)}
    >
      <h3 id={id} className="flex items-center gap-2 type-caption text-muted-foreground">
        {alert && <TriangleAlertIcon aria-hidden className="size-5 shrink-0 text-no-text" />}
        {label}
        {alert && <span className="sr-only">, above the alert threshold</span>}
      </h3>
      <p className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <span className="type-number-lg">{figure}</span>{" "}
        {beside !== undefined && (
          <span className="inline-flex items-center gap-1 type-body-sm text-muted-foreground">
            {beside}
          </span>
        )}
      </p>
      {children}
    </Card>
  );
}

/** One of the parts a figure is made of. */
function Part({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3 border-t py-2 last:pb-0">
      <dt className="type-body-sm text-muted-foreground">{label}</dt>
      <dd>
        <Amount value={value} />
      </dd>
    </div>
  );
}

/**
 * The tiles of the risk page (DESIGN.md §13). Every figure is a field of `GET
 * /admin/risk`, shown as sent: a figure and its limit stand side by side and the page
 * compares nothing. Only Obligations has a warning state, because only for it the API
 * sends a flag (`above_threshold`). The month's net adjustments of balances stand in the
 * tile of the codes: both are tokens VIS handed out by hand.
 */
function Tiles({ risk }: { risk: RiskResponse }) {
  const { obligations } = risk;
  const id = useId();

  return (
    <section aria-labelledby={id}>
      <h2 id={id} className="sr-only">
        Figures
      </h2>
      <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-4">
        <Tile
          label="Obligations"
          figure={<Amount value={obligations.total_cr} size="lg" />}
          beside={
            <>
              Alert above <Amount value={obligations.alert_threshold_cr} size="sm" />
            </>
          }
          alert={obligations.above_threshold}
          className="md:col-span-2 lg:row-span-2"
        >
          <dl className="flex flex-col">
            <Part label="Balances" value={obligations.user_balances_cr} />
            <Part label="Vouchers not used yet" value={obligations.pending_vouchers_cr} />
            <Part label="Markets" value={obligations.market_exposure_cr} />
          </dl>
        </Tile>
        <Tile
          label="Loss budget"
          figure={<Amount value={risk.loss_budget_cr} size="lg" />}
          beside={
            <>
              of <Amount value={risk.max_loss_budget_cr} size="sm" />
            </>
          }
        />
        <Tile
          label="Open markets"
          figure={formatCount(risk.open_markets)}
          beside={<>of {formatCount(risk.max_open_markets)}</>}
        />
        <Tile
          label="Codes not redeemed yet"
          figure={<Amount value={risk.unredeemed_code_value_cr} size="lg" />}
        />
        <Tile
          label="Codes this month"
          figure={<Amount value={risk.code_issuance_this_month_cr} size="lg" />}
          beside={
            <>
              of <Amount value={risk.max_code_issuance_per_month_cr} size="sm" />
            </>
          }
        >
          {/* What admins added to balances and took from them by hand: it counts against
              no cap, so it has no "of", and a net below zero keeps its minus sign. */}
          <dl className="flex flex-col">
            <Part label="Adjustments this month" value={risk.adjustments_this_month_cr} />
          </dl>
        </Tile>
      </div>
    </section>
  );
}

/* ---- Alerts ----------------------------------------------------------------------- */

/**
 * `severity` is a key, not copy. INFO and WARNING are neutral banners with different
 * icons, CRITICAL is the error banner; the word is for a screen reader, which sees
 * neither the icon nor the colour.
 */
const SEVERITIES: Record<
  AlertSeverity,
  { tone: NonNullable<BannerProps["tone"]>; icon: LucideIcon; word: string }
> = {
  INFO: { tone: "neutral", icon: InfoIcon, word: "Information" },
  WARNING: { tone: "neutral", icon: TriangleAlertIcon, word: "Warning" },
  CRITICAL: { tone: "error", icon: OctagonAlertIcon, word: "Critical" },
};

/** A value of an alert's `context`, as it was sent. */
function shown(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value);
}

/**
 * One alert nobody acknowledged yet: its `message`, `created_at` and the keys and values
 * of its `context`, with "Acknowledge", which sends `POST /admin/alerts/{alert_id}/ack`.
 * Once that is answered the alert is done with; the list is read again and drops it.
 */
function Alert({ alert }: { alert: AlertItem }) {
  const acknowledge = useSubmit(acknowledgeAlertMutation);
  const wait = useRetryAfter(acknowledge.error);
  const { tone, icon, word } = SEVERITIES[alert.severity];
  const context = Object.entries(alert.context);

  if (acknowledge.isSuccess) return null;

  return (
    <li>
      <Banner
        tone={tone}
        icon={icon}
        action={
          <Button
            variant="secondary"
            size="compact"
            width="fit"
            loading={acknowledge.isPending}
            disabled={wait > 0}
            onClick={() => {
              // A second tap sends nothing while the first is out (DESIGN.md §14).
              acknowledge.mutate(alert.id);
            }}
          >
            Acknowledge
          </Button>
        }
      >
        <div className="flex flex-col gap-2">
          <p>
            <span className="sr-only">{word}: </span>
            {alert.message}
          </p>
          <DateTime value={alert.created_at} className="type-caption" />
          {context.length > 0 && (
            <dl className="flex flex-col gap-1 type-caption">
              {context.map(([key, value]) => (
                <div key={key} className="flex flex-wrap gap-x-2">
                  <dt>{key}</dt>
                  <dd className="min-w-0 font-display wrap-break-word tabular-nums">
                    {shown(value)}
                  </dd>
                </div>
              ))}
            </dl>
          )}
          {acknowledge.error !== null && (
            <div className="flex flex-col gap-1">
              <InlineError>{messageOf(acknowledge.error)}</InlineError>
              <RetryCountdown error={acknowledge.error} />
            </div>
          )}
        </div>
      </Banner>
    </li>
  );
}

/* ---- Markets ---------------------------------------------------------------------- */

/** `close_at` in a narrow column: the date, and under it the time of day. */
function Closes({ value }: { value: string }) {
  const { timezone } = useConfig();
  return (
    <time dateTime={value}>
      <span className="block">{formatDate(value, timezone)}</span>{" "}
      <span className="block type-caption text-muted-foreground">
        {formatTime(value, timezone)}
      </span>
    </time>
  );
}

/**
 * One row per market that is not settled, in the ten columns of DESIGN.md §13: the
 * question with its status badge under it, then what an admin reads first (the chance,
 * the volume, the exposure and what a void would cost), then the share counts, how the
 * market is priced, its account and when it closes. Two values share a cell where they
 * belong together, so that the table fits 1024 px. A figure the API sends with a minus
 * sign keeps it.
 */
const COLUMNS: readonly Column<RiskMarketRow>[] = [
  {
    header: "Question",
    wide: true,
    // Nine columns of figures stand beside it: it wraps into the room they leave. From
    // 1024 px it asks for less, so that the table fits its box whatever the figures are.
    className: "min-w-40 lg:min-w-28",
    cell: (market) => (
      <>
        <span className="wrap-break-word">{market.question}</span>{" "}
        <StatusBadge status={market.status} className="mt-1" />
      </>
    ),
  },
  { header: "Chance", numeric: true, cell: (market) => <Chance value={market.p_yes} /> },
  { header: "Volume", numeric: true, cell: (market) => <Amount value={market.volume_cr} /> },
  { header: "Exposure", numeric: true, cell: (market) => <Amount value={market.exposure_cr} /> },
  { header: "Void cost", numeric: true, cell: (market) => <Amount value={market.void_cost_cr} /> },
  {
    header: "Shares",
    numeric: true,
    cell: (market) => (
      <>
        <span className="block">{formatAmount(market.q_yes)} YES</span>{" "}
        <span className="block">{formatAmount(market.q_no)} NO</span>
      </>
    ),
  },
  {
    header: "Tier",
    cell: (market) =>
      market.tier === null ? (
        <Missing />
      ) : (
        <>
          <span className="block">{tierName(market.tier)}</span>{" "}
          <span className="block type-caption text-muted-foreground">
            b {formatAmount(market.b_cr)}
          </span>
        </>
      ),
  },
  { header: "Loss budget", numeric: true, cell: (market) => <Amount value={market.budget_cr} /> },
  {
    header: "Account",
    numeric: true,
    cell: (market) => <Amount value={market.account_balance_cr} />,
  },
  { header: "Closes", cell: (market) => <Closes value={market.close_at} /> },
];

function Section({ title, children }: { title: string; children: ReactNode }) {
  const id = useId();
  return (
    <section aria-labelledby={id} className="flex flex-col gap-2">
      <h2 id={id} className="type-h3">
        {title}
      </h2>
      {children}
    </section>
  );
}

/** Blocks in the shape of the tiles, an alert and the table. */
function RiskSkeleton() {
  return (
    <div data-slot="risk-skeleton" className="flex flex-col gap-6">
      <div aria-hidden className="grid gap-3 md:grid-cols-2 lg:grid-cols-4">
        <Skeleton className="h-52 rounded-lg md:col-span-2 lg:row-span-2" />
        <Skeleton className="h-24 rounded-lg" />
        <Skeleton className="h-24 rounded-lg" />
        <Skeleton className="h-24 rounded-lg" />
        <Skeleton className="h-24 rounded-lg" />
      </div>
      <Skeleton className="h-16 w-full" />
      <DataTableSkeleton columns={COLUMNS} dense />
    </div>
  );
}

/**
 * Admin: the risk panel (`/admin/risk`, DESIGN.md §13). The tiles, then the alerts nobody
 * acknowledged yet, then the table of the markets that are not settled, in the API's
 * order. The table fits 1024 px without scrolling sideways; on anything narrower it
 * scrolls inside its own box. A row opens the admin view of its market.
 *
 * Every figure is the API's, shown as sent. The page refetches when the window gets the
 * focus back; nothing on it is live.
 */
export function AdminRiskScreen() {
  const risk = useQuery(riskQuery);

  return (
    <Screen top="main" title="Risk">
      <AdminTabs />
      <QueryState query={risk} skeleton={<RiskSkeleton />}>
        {(data) => (
          <>
            <Tiles risk={data} />
            <Section title="Alerts">
              {data.alerts.length === 0 ? (
                <EmptyLine>No alerts.</EmptyLine>
              ) : (
                <ul className="flex flex-col gap-3">
                  {data.alerts.map((alert) => (
                    <Alert key={alert.id} alert={alert} />
                  ))}
                </ul>
              )}
            </Section>
            <Section title="Markets">
              {data.markets.length === 0 ? (
                <EmptyLine>No markets.</EmptyLine>
              ) : (
                <DataTable
                  label="Markets"
                  columns={COLUMNS}
                  rows={data.markets}
                  rowKey={(market) => market.id}
                  rowHref={(market) => `/admin/markets/${market.id}`}
                  dense
                />
              )}
            </Section>
          </>
        )}
      </QueryState>
    </Screen>
  );
}
