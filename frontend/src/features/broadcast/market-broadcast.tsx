"use client";

import { useQuery } from "@tanstack/react-query";
import {
  type CSSProperties,
  type ReactNode,
  useEffect,
  useEffectEvent,
  useId,
  useRef,
  useState,
} from "react";

import { LiveBadge, StatusBadge } from "@/components/market";
import { Link, TextLink, useHideTabBar } from "@/components/shell";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ChatPanel } from "@/features/chat";
import { marketBroadcastQuery, marketQuery } from "@/lib/api/queries";
import type { BroadcastConfig, BroadcastEndReason, BroadcastItem } from "@/lib/api/schema";
import { useConfig } from "@/lib/api/session";
import { WIDE_QUERY } from "@/lib/design";
import { formatCount } from "@/lib/format";
import { useMediaQuery } from "@/lib/hooks";
import { liveTopic, useLiveTopic } from "@/lib/live";
import { cn } from "@/lib/utils";

import { BroadcastPlayer } from "./broadcast-player";
import { BroadcastRequest } from "./broadcast-request";

export type MarketBroadcastProps = {
  /** The id of the market whose live stream this is. */
  marketId: string;
  /**
   * True while the screen keeps its two outcome buttons at its lower edge whenever their
   * place is out of view, as Market detail does for a market that trades. While its
   * viewer watches on a phone, the chat under the picture then ends above those buttons.
   */
  pinnedButtons?: boolean;
  /**
   * Told when the viewer starts to watch and when that ends: with "Stop watching", with
   * the end of the live stream, and when the section goes. Below 768 px the screen keeps
   * its outcome buttons on screen at every scroll position in that time.
   */
  onWatchingChange?: (watching: boolean) => void;
  /**
   * Told when the composer of the chat takes the focus and when it loses it: what the
   * screen pins gives way to the keyboard meanwhile.
   */
  onTypingChange?: (typing: boolean) => void;
};

/** The studio of a market's live stream. */
const studioOf = (marketId: string) => `/markets/${marketId}/live`;

/**
 * The sentence of an end reason (DESIGN.md §13), with the two figures of `/config`
 * `broadcast`. A live stream its broadcaster ended has none.
 */
function endSentence(reason: BroadcastEndReason | null, config: BroadcastConfig): string | null {
  switch (reason) {
    case "STALLED":
      return config.stall_seconds === 1
        ? "No video arrived for 1 second."
        : `No video arrived for ${formatCount(config.stall_seconds)} seconds.`;
    case "TIME_LIMIT":
      return config.max_minutes === 1
        ? "It reached the limit of 1 minute."
        : `It reached the limit of ${formatCount(config.max_minutes)} minutes.`;
    case "ADMIN":
      return "VIS ended it.";
    default:
      return null;
  }
}

/**
 * The video box while its viewer watches on a phone (DESIGN.md §13, "Watching on a phone"):
 * 16:9 of the content column, which is the window less its two 16 px gutters, so 343 x
 * 193 px at 375 px. On a screen that is wider than it is tall (a phone held sideways) the
 * box is at most 45 % of the screen's height: it sticks, and a box as tall as the screen
 * would leave nothing of the page in sight. The picture is fitted into the box either way.
 */
const BOX_ON_A_PHONE = "max-h-[45dvh]";
const BOX_HEIGHT = "min((100vw - 2rem) * 9 / 16, 45dvh)";

/**
 * What stands under the box is then at least as tall as what is left of the screen: the
 * screen, less the top bar, the box, the 8 px under the box, 8 px above the lower edge,
 * the room of the tab bar and, where the screen pins them, the 73 px of the outcome
 * buttons. "Watch live" brings it right under the box that sticks: its place when the page
 * is scrolled to it lies below the top bar by the box and the 8 px under it.
 */
const underTheBox = (pinnedButtons: boolean) =>
  ({
    "--live-stream-box": BOX_HEIGHT,
    minHeight: `calc(100dvh - var(--top-bar-space) - var(--tab-bar-space) - var(--live-stream-box) - ${
      pinnedButtons ? "5.5625rem" : "1rem"
    })`,
    scrollMarginTop: "calc(var(--live-stream-box) + 0.5rem)",
  }) as CSSProperties;

/**
 * The card and the chat under it. While its viewer watches on a phone they are two rows of
 * a grid that is as tall as what is left: the card as tall as it is, and the chat the
 * rest, never less than 200 px. The chat then fills its row without adding to its height
 * (`CHAT_FILLS`), so the row is as tall as the rule says and not as the messages are, and
 * a screen too short for both grows the grid in place of laying the chat over what
 * follows.
 */
const STACKED = "flex min-w-0 flex-col gap-3";
const WATCHED_ON_A_PHONE = "grid min-w-0 grid-rows-[auto_minmax(12.5rem,1fr)] gap-2";
const CHAT_FILLS = { row: "relative min-w-0", column: "absolute inset-0 flex flex-col" };
const CHAT_STANDS = { row: "min-w-0", column: "flex flex-col" };

const LINE = "type-body-sm text-muted-foreground";

/**
 * The rows of the proposer and of whoever streams (DESIGN.md §13, the second table): what
 * became of a live stream of the viewer's own, by its status. Null where the status has
 * no row, and for everybody else.
 */
function ownRows(
  broadcast: BroadcastItem,
  canStart: boolean,
  config: BroadcastConfig,
): ReactNode | null {
  const studio = studioOf(broadcast.market_id);
  const setUp = canStart && (
    <Button variant="secondary" asChild>
      <Link href={studio}>Set up the live stream</Link>
    </Button>
  );

  switch (broadcast.status) {
    case "REQUESTED":
      return (
        <>
          {/* The status badge of a proposal in review: "In review". */}
          <StatusBadge status="PROPOSED" />
          <p className={LINE}>You asked for a live stream. VIS decides on it.</p>
        </>
      );
    case "APPROVED":
      return (
        <>
          <p className={LINE}>Your live stream is approved. Start it when you are ready.</p>
          {setUp}
        </>
      );
    case "REFUSED":
      return (
        <div className="flex flex-col gap-1">
          <p className="type-body-sm">Your request was not approved.</p>
          {broadcast.refusal_reason !== null && (
            <p className={cn(LINE, "wrap-break-word whitespace-pre-line")}>
              {broadcast.refusal_reason}
            </p>
          )}
        </div>
      );
    case "LIVE":
      return (
        <div className="flex flex-wrap items-center justify-between gap-x-3">
          <p className="type-body-sm">You are live.</p>
          <TextLink href={studio} className="type-body-sm">
            Open the studio
          </TextLink>
        </div>
      );
    case "ENDED": {
      const sentence = endSentence(broadcast.end_reason, config);
      return (
        <>
          <p className={LINE}>
            Your live stream has ended.
            {sentence !== null && ` ${sentence}`}
            {canStart && " You can go live again without asking."}
          </p>
          {setUp}
        </>
      );
    }
  }
}

/**
 * The section "Live stream" of Market detail (DESIGN.md §13): what
 * `GET /markets/{market_id}/broadcast` says about the live stream of a market, and the
 * live chat directly under it while the stream is on. It reads the flags of `viewer` and
 * shows a button exactly when its flag is true; it holds no role, no status of the market
 * and no clock against anything.
 *
 * **It has content or is absent.** While its read loads or fails, while there is nothing
 * to say, and whenever the read answers `NOT_FOUND` (as for a proposal), it renders
 * nothing: no frame, no banner, no sentence.
 *
 * **Video starts on "Watch live" and never by itself**: no playlist is asked for before
 * the tap, and "Stop watching" takes the player away. The wish to watch belongs to one
 * live stream: a stream that starts later on the same market waits for a tap of its own.
 *
 * **Live.** The section names `broadcast:{id}` to the app's event stream, and the rules of
 * `lib/live` read its answer again after a `broadcast.changed` of its market, whenever the
 * stream opens and at every poll. That is how "The live stream is starting." turns into
 * "Watch live", how a viewer learns that a stream ended, and how an approval reaches the
 * page of the proposer.
 *
 * **Watching on a phone** (below 768 px): the video box leaves the card, moves to the top
 * of the column it stands in and sticks under the top bar at every scroll position; the
 * tab bar is hidden; the card is one row; and the chat takes the height that is left, at
 * least 200 px. The section therefore renders its parts side by side, without a frame
 * around them: it stands directly in a flex column with 24 px between its items, as the
 * sections of Market detail do. From 768 px nothing sticks and the box stands in the card.
 */
export function MarketBroadcast(props: MarketBroadcastProps) {
  // What the section remembers (the wish to watch, a live stream that was on) belongs to
  // one market.
  return <Section key={props.marketId} {...props} />;
}

function Section({
  marketId,
  pinnedButtons = false,
  onWatchingChange,
  onTypingChange,
}: MarketBroadcastProps) {
  const titleId = useId();
  const config = useConfig().broadcast;
  const wide = useMediaQuery(WIDE_QUERY);

  const query = useQuery(marketBroadcastQuery(marketId));
  useLiveTopic(liveTopic.broadcast(marketId));
  // The question names the video box. Market detail reads the market; this asks for nothing.
  const question = useQuery({ ...marketQuery(marketId), enabled: false }).data?.question;

  const answer = query.data;
  const broadcast = answer?.broadcast ?? null;
  const live = broadcast?.status === "LIVE";

  // "It was LIVE while this screen was open": remembered from the answers that came.
  const [wasLive, setWasLive] = useState(false);
  if (live && !wasLive) setWasLive(true);

  // The live stream the viewer asked to watch, by its id.
  const [asked, setAsked] = useState<string | null>(null);
  const playlistUrl = live && answer?.viewer.can_watch === true ? broadcast.playlist_url : null;
  const watching = playlistUrl !== null && broadcast !== null && asked === broadcast.id;
  const onPhone = watching && !wide;

  useHideTabBar(onPhone);

  const tellWatching = useEffectEvent((value: boolean) => onWatchingChange?.(value));
  const tellTyping = useEffectEvent((value: boolean) => onTypingChange?.(value));
  useEffect(() => {
    if (!watching) return;
    tellWatching(true);
    return () => {
      tellWatching(false);
      // A composer that goes with its chat says nothing when it goes.
      tellTyping(false);
    };
  }, [watching]);

  // On a phone "Watch live" brings the card right under the box, so that the picture, the
  // chat and what the screen pins are on screen together.
  const rest = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (onPhone) rest.current?.scrollIntoView({ block: "start" });
  }, [onPhone]);

  // Nothing while the read loads or fails, and nothing for a market the API does not show.
  if (answer === undefined || query.error?.code === "NOT_FOUND") return null;

  const { viewer } = answer;
  const own = broadcast !== null && viewer.is_broadcaster;
  const ownPart = own ? ownRows(broadcast, viewer.can_start, config) : null;
  // The request form: for the proposer without a live stream, and again under a request
  // that was refused or a live stream that ended. A live stream that ended by itself and
  // may go on needs no request: the server says both then, and the section offers the
  // way that asks nobody.
  const asksAgain =
    !own || broadcast.status === "REFUSED" || (broadcast.status === "ENDED" && !viewer.can_start);
  const canRequest = viewer.can_request && asksAgain;
  // The viewer's own "has ended" says it for whoever streamed.
  const ended = !live && wasLive && !(own && broadcast.status === "ENDED");

  const starting = live && !broadcast.on_air;
  const full = live && broadcast.on_air && viewer.full && !watching;
  const invited = live && broadcast.on_air && !watching && !full;

  if (!live && !ended && ownPart === null && !canRequest) return null;

  const player = watching && (
    <BroadcastPlayer
      marketId={marketId}
      playlistUrl={playlistUrl}
      question={question}
      className={onPhone ? BOX_ON_A_PHONE : undefined}
    />
  );
  const stopWatching = (
    <Button variant="ghost" size="compact" width="fit" onClick={() => setAsked(null)}>
      Stop watching
    </Button>
  );
  const behind = <p className="type-caption text-muted-foreground">Live, a few seconds behind.</p>;

  const chat = onPhone ? CHAT_FILLS : CHAT_STANDS;

  return (
    <>
      {onPhone && (
        <div
          key="box"
          data-slot="live-stream-box"
          className="sticky top-(--top-bar-space) z-30 order-first -mt-4 bg-background pb-2"
        >
          {player}
        </div>
      )}

      <div
        key="rest"
        ref={rest}
        className={onPhone ? WATCHED_ON_A_PHONE : STACKED}
        style={onPhone ? underTheBox(pinnedButtons) : undefined}
      >
        <section aria-labelledby={titleId} data-slot="live-stream">
          <Card>
            <div className="flex items-center justify-between gap-3">
              <div className="flex min-w-0 flex-col gap-1">
                <div className="flex items-center gap-2">
                  <h2 id={titleId} className="type-h3">
                    Live stream
                  </h2>
                  {live && <LiveBadge />}
                </div>
                {onPhone && behind}
              </div>
              {onPhone && stopWatching}
            </div>

            {ownPart}

            {starting && (
              <p role="status" className={LINE}>
                The live stream is starting.
              </p>
            )}

            {invited && (
              <>
                <p className={LINE}>A live stream is on for this market.</p>
                {viewer.can_watch && broadcast.playlist_url !== null && (
                  <Button variant="secondary" onClick={() => setAsked(broadcast.id)}>
                    Watch live
                  </Button>
                )}
              </>
            )}

            {watching && wide && (
              <>
                {player}
                <div className="flex items-center justify-between gap-3">
                  {behind}
                  {stopWatching}
                </div>
              </>
            )}

            {full && (
              <>
                <p className={LINE}>The live stream is full right now. Try again in a moment.</p>
                <Button
                  variant="secondary"
                  loading={query.isFetching}
                  onClick={() => void query.refetch()}
                >
                  Try again
                </Button>
              </>
            )}

            {ended && (
              <p role="status" className={LINE}>
                The live stream has ended.
              </p>
            )}

            {canRequest && <BroadcastRequest marketId={marketId} />}
          </Card>
        </section>

        {live && (
          <div data-slot="live-stream-chat" className={chat.row}>
            <div className={chat.column}>
              <ChatPanel
                marketId={marketId}
                broadcastId={broadcast.id}
                fill={onPhone}
                onComposerFocus={onTypingChange}
              />
            </div>
          </div>
        )}
      </div>
    </>
  );
}
