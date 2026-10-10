"use client";

import { useQuery } from "@tanstack/react-query";

import { QueryState } from "@/components/feedback";
import { Amount } from "@/components/money";
import { Screen } from "@/components/shell";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { positionsQuery } from "@/lib/api/queries";

import { HistoryTab } from "./history-tab";
import { PositionsTab } from "./positions-tab";

/**
 * Portfolio (`/portfolio`): DESIGN.md §13. Top to bottom: the summary card, then the two
 * views of the screen as tabs. **Positions** is `GET /me/positions`, which also holds the
 * figure of the summary card; **History** is `GET /me/trades`, asked for when its tab is
 * opened and page by page from there.
 *
 * Every figure is the API's: what the open positions are worth is `total_value_cr`, and
 * the screen adds up nothing.
 */
export function PortfolioScreen() {
  const positions = useQuery(positionsQuery);

  return (
    <Screen top="main" title="Portfolio" width="narrow">
      {/* A failed read is said once, in the Positions tab, with "Try again". */}
      <QueryState query={positions} skeleton={<SummarySkeleton />} onError={() => null}>
        {(portfolio) => <Summary total={portfolio.total_value_cr} />}
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

/** The summary card: what the positions in markets that are not settled are worth now. */
function Summary({ total }: { total: string }) {
  return (
    <Card data-slot="portfolio-summary">
      <dl className="flex flex-col gap-1">
        <dt className="type-body-sm text-muted-foreground">Value of open positions</dt>
        <dd>
          <Amount value={total} size="lg" />
        </dd>
      </dl>
    </Card>
  );
}

function SummarySkeleton() {
  return (
    <Card data-slot="portfolio-summary-skeleton" aria-hidden className="gap-1">
      <Skeleton className="h-5 w-44" />
      <Skeleton className="h-7 w-24" />
    </Card>
  );
}
