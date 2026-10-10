"use client";

import { type ReactNode, useEffect, useEffectEvent, useRef } from "react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import type { ApiError } from "@/lib/api/errors";

import { ErrorBanner } from "./banner";

/** What `useInfiniteQuery` returns, as far as this component looks. */
type Paged = {
  hasNextPage: boolean;
  isFetchingNextPage: boolean;
  isFetchNextPageError: boolean;
  error: ApiError | null;
  fetchNextPage: () => unknown;
};

const hasObserver = typeof IntersectionObserver !== "undefined";

/**
 * The end of a list that pages (DESIGN.md §12, List rows): when the user reaches it, the
 * next page is asked for with the `next_cursor` of the last one. Put it after the rows
 * of `useInfiniteQuery`; it renders nothing once the last page is there.
 *
 * While the next page loads it shows `skeleton` (one row by default). A page that failed
 * shows the API's message with "Try again" and asks for nothing by itself.
 */
export function LoadMore({ query, skeleton }: { query: Paged; skeleton?: ReactNode }) {
  const { hasNextPage, isFetchingNextPage, isFetchNextPageError } = query;
  const sentinel = useRef<HTMLDivElement>(null);
  const waiting = hasNextPage && !isFetchingNextPage && !isFetchNextPageError;

  const next = useEffectEvent(() => {
    void query.fetchNextPage();
  });

  useEffect(() => {
    // Observing afresh after every page reports a sentinel that is still in view.
    const node = sentinel.current;
    if (!waiting || node === null || !hasObserver) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) next();
      },
      { rootMargin: "200px" },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [waiting]);

  if (!hasNextPage) return null;

  if (isFetchNextPageError && query.error !== null) {
    return <ErrorBanner error={query.error} onRetry={() => void query.fetchNextPage()} />;
  }

  return (
    <div ref={sentinel} data-slot="load-more" aria-busy={isFetchingNextPage}>
      {isFetchingNextPage && (skeleton ?? <Skeleton className="my-2 h-10 w-full" />)}
      {!hasObserver && !isFetchingNextPage && (
        <Button variant="secondary" onClick={() => void query.fetchNextPage()}>
          Show more
        </Button>
      )}
    </div>
  );
}
