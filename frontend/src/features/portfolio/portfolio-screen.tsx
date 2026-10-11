"use client";

import { useQuery } from "@tanstack/react-query";

import { QueryState } from "@/components/feedback";
import { Amount } from "@/components/money";
import { Screen } from "@/components/shell";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { positionsQuery } from "@/lib/api/queries";
import type { PortfolioResponse } from "@/lib/api/schema";

import { EquityChart } from "./equity-chart";
import { HistoryTab } from "./history-tab";
import { PositionsTab } from "./positions-tab";

/**
 * Portfolio (`/portfolio`): DESIGN.md §13. Top to bottom: the summary card, then the two
 * views of the screen as tabs. **Positions** is `GET /me/positions`, which also holds the
 * figure of the summary card; **History** is `GET /me/trades`, asked for when its tab is
 * opened and page by page from there.
 *
 * Every figure and the equity history come from the API; the screen adds up nothing.
 */
export function PortfolioScreen() {
  const positions = useQuery(positionsQuery);

  return (
    <Screen top="main" title="Portfolio" width="narrow">
      {/* A failed read is said once, in the Positions tab, with "Try again". */}
      <QueryState query={positions} skeleton={<SummarySkeleton />} onError={() => null}>
        {(portfolio) => <Summary portfolio={portfolio} />}
      </QueryState>

      <Tabs defaultValue="positions">
        <TabsList aria-label="Portfolio">
          <TabsTrigger value="positions">Positions</TabsTrigger>
          <TabsTrigger value="history">History</TabsTrigger>
        </TabsList>
        <TabsContent value="positions">
          <PositionsTab positions={positions} />
        </TabsContent>
        <TabsContent value="history">
          <HistoryTab />
        </TabsContent>
      </Tabs>
    </Screen>
  );
}

/** Total equity, its two parts and the history, all as valued by the server. */
function Summary({ portfolio }: { portfolio: PortfolioResponse }) {
  return (
    <Card data-slot="portfolio-summary">
      <dl className="flex flex-col gap-1">
        <dt className="type-body-sm text-muted-foreground">Total equity</dt>
        <dd>
          <Amount value={portfolio.total_equity_cr} size="lg" />
        </dd>
      </dl>
      <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div data-slot="portfolio-positions-value" className="flex flex-col gap-1">
          <dt className="type-body-sm text-muted-foreground">Value of open positions</dt>
          <dd>
            <Amount value={portfolio.total_value_cr} size="lg" />
          </dd>
        </div>
        <div className="flex flex-col gap-1">
          <dt className="type-body-sm text-muted-foreground">Remaining tokens</dt>
          <dd>
            <Amount value={portfolio.balance_cr} size="lg" />
          </dd>
        </div>
      </dl>
      <EquityChart portfolio={portfolio} />
    </Card>
  );
}

function SummarySkeleton() {
  return (
    <Card data-slot="portfolio-summary-skeleton" aria-hidden>
      <div className="flex flex-col gap-1">
        <Skeleton className="h-5 w-44" />
        <Skeleton className="h-7 w-24" />
      </div>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Skeleton className="h-13 w-44" />
        <Skeleton className="h-13 w-44" />
      </div>
      <Skeleton className="h-50 w-full md:h-70" />
    </Card>
  );
}
