"use client";

import { useQuery } from "@tanstack/react-query";
import { ChevronRightIcon } from "lucide-react";

import { LiveBadge } from "@/components/market";
import { Link } from "@/components/shell";
import { liveBroadcastsQuery } from "@/lib/api/queries";
import { liveTopic, useLiveTopic } from "@/lib/live";

/**
 * "Live now" on Markets (DESIGN.md §13): one row per item of `GET /broadcasts/live`, in
 * the order the API returns them, 8 px apart, between the top bar and the filter chips. A
 * row is one link, 44 px tall and as wide as the content column: the Live badge, the
 * question on one line, cut with an ellipsis, and a chevron. It leads to the market and is
 * named "Live now: {question}". No picture of a live stream stands in a row (§8).
 *
 * **It has content or is absent.** While the read loads, answers no item or fails,
 * nothing is shown and nothing is said: the list under it has its own four states, and a
 * market's own page shows its live stream either way.
 *
 * It names `markets` to the app's event stream: when a live stream starts, is on or ends,
 * the rules of `lib/live` read the list again, as they do whenever the stream opens and at
 * every poll.
 */
export function LiveNowRow() {
  const { data } = useQuery(liveBroadcastsQuery);
  useLiveTopic(liveTopic.markets());

  if (data === undefined || data.items.length === 0) return null;

  return (
    <ul data-slot="live-now" className="flex flex-col gap-2">
      {data.items.map((item) => (
        <li key={item.broadcast_id} className="min-w-0">
          <Link
            href={`/markets/${item.market_id}`}
            aria-label={`Live now: ${item.question}`}
            className="flex h-11 items-center gap-3 rounded-lg border bg-card px-4 text-card-foreground transition-colors duration-(--duration-fast) hover:bg-raised active:bg-raised"
          >
            <LiveBadge />
            <span className="min-w-0 flex-1 truncate type-h3">{item.question}</span>
            <ChevronRightIcon aria-hidden className="size-5 shrink-0 text-muted-foreground" />
          </Link>
        </li>
      ))}
    </ul>
  );
}
