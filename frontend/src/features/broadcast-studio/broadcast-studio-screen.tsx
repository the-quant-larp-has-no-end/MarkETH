"use client";

import { useQuery } from "@tanstack/react-query";
import { type ReactNode, useEffect, useState, useSyncExternalStore } from "react";

import { Banner, ErrorBanner, SubmitError } from "@/components/feedback";
import { LiveBadge } from "@/components/market";
import { Link, NotFoundScreen, Screen, useHideTabBar } from "@/components/shell";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { type ApiError, messageOf } from "@/lib/api/errors";
import { startBroadcastAsAdminMutation, startBroadcastMutation } from "@/lib/api/mutations";
import { marketBroadcastQuery, marketQuery } from "@/lib/api/queries";
import type { MarketBroadcastResponse } from "@/lib/api/schema";
import { useConfig } from "@/lib/api/session";
import { useSubmit } from "@/lib/api/submit";
import { formatTime } from "@/lib/format";
import { liveTopic, useLiveTopic } from "@/lib/live";

import { type CaptureRefusal, REFUSALS } from "./capture";
import { EndStream } from "./end-stream";
import { Preview } from "./preview";
import { ProposerBroadcast, showsProposer } from "./proposer-broadcast";
import { Studio, type StudioState } from "./studio";
import { endReasonSentence, minutesOf, secondsOf } from "./words";

/** Which of its states the studio shows (DESIGN.md §13). */
type View = "setup" | "live" | "paused" | "ended" | "proposer" | "none";

/**
 * What the answer of the API and what this page has done make together.
 *
 * Whether a live stream is on, and whose it is, is the answer's. The page adds only what
 * the API cannot know: whether this tab is the one that sends, and whether the stream it
 * sent to is over.
 */
function viewOf(
  { broadcast, viewer }: MarketBroadcastResponse,
  phase: StudioState["phase"],
  startRefused: boolean,
): View {
  const onAir = broadcast?.status === "LIVE" && viewer.is_broadcaster;
  if (phase === "over") return "ended";
  if (onAir) return phase === "live" ? "live" : "paused";
  // The page was sending, and the answer says that the stream is not on.
  if (phase === "live" || phase === "paused") return "ended";
  // A start the API refused keeps the set-up, with the API's sentence above its button.
  if (viewer.can_start || viewer.can_start_as_admin || startRefused) return "setup";
  return showsProposer({ broadcast, viewer }) ? "proposer" : "none";
}

/**
 * The studio of a live stream (`/markets/[id]/live`): DESIGN.md §13. Where a live stream
 * is sent from: the browser records the camera and the microphone and sends the video up
 * in small pieces, and the server relays it to the viewers.
 *
 * It is for a caller the API lets start a live stream on this market (`can_start`,
 * `can_start_as_admin`), for whoever is on air with it, and for the market's proposer,
 * who asks for one here. Anybody else gets the "not found" screen. The studio reads the
 * flags and holds no role: a button stands exactly where its flag is true.
 */
export function BroadcastStudioScreen({ marketId }: { marketId: string }) {
  // The camera, the recorder and the upload belong to one market: another market is
  // another studio, with nothing of the one before.
  return <StudioOf key={marketId} marketId={marketId} />;
}

function StudioOf({ marketId }: { marketId: string }) {
  const { broadcast: config } = useConfig();
  const [studio] = useState(() => new Studio(config));
  const state = useSyncExternalStore(studio.subscribe, studio.getState, studio.getState);
  const read = useQuery(marketBroadcastQuery(marketId));
  const market = useQuery(marketQuery(marketId));
  const startOwn = useSubmit(startBroadcastMutation);
  const startAsAdmin = useSubmit(startBroadcastAsAdminMutation);
  const [pictured, setPictured] = useState<MediaStream | null>(null);
  const [held, setHeld] = useState(false);

  const answer = read.data;
  // The caller has one of the two starts. Which one, the answer says at the tap.
  const startError = startAsAdmin.error ?? startOwn.error;
  const startPending = startAsAdmin.isPending || startOwn.isPending;
  const startRefused = startError !== null;
  const told = answer === undefined ? null : viewOf(answer, state.phase, startRefused);
  // A page that showed what became of a request does not turn its camera on by itself
  // when the request is approved: the set-up then waits for "Set up the live stream".
  if (told === "proposer" && !held) setHeld(true);
  const view = told === "setup" && held && !startRefused ? "proposer" : told;

  // While the portal has no session, nothing of this page asks the API but the upload:
  // any other call would reload the page, and a reload ends the camera (DESIGN.md §14).
  useLiveTopic(state.signedOut ? null : liveTopic.broadcast(marketId));
  useHideTabBar(view === "live");

  // The screen is left: the camera and the upload are let go.
  useEffect(() => () => studio.close(), [studio]);

  // The API says the stream this page sends to is not on: an admin ended it, or it ended
  // by itself. The page stops sending and lets its camera go.
  const endedElsewhere = view === "ended" && state.phase !== "over";
  useEffect(() => {
    if (endedElsewhere) studio.finish();
  }, [endedElsewhere, studio]);

  // The API refused a piece: the answer on screen is behind. It is read again, and says
  // why the stream ended and whether it may go on.
  const { refetch } = read;
  const refused = state.refused;
  useEffect(() => {
    if (refused !== null) void refetch();
  }, [refused, refetch]);

  if (read.error?.code === "NOT_FOUND" && answer === undefined) return <NotFoundScreen />;
  if (view === "none") return <NotFoundScreen />;

  const frame = (children: ReactNode) => (
    <Screen top="inner" title="Live stream" backTo={`/markets/${marketId}`} width="narrow">
      {children}
    </Screen>
  );

  if (answer === undefined || view === null) {
    return frame(
      read.error !== null ? (
        <ErrorBanner error={read.error} onRetry={() => void read.refetch()} />
      ) : (
        <StudioSkeleton />
      ),
    );
  }

  const { broadcast, viewer } = answer;
  const canStart = viewer.can_start || viewer.can_start_as_admin;

  /**
   * "Go live": the camera first, then the start. The recorder runs only once the API has
   * answered that the stream is LIVE, and its pieces go to the id of that answer: a
   * stream that goes live again is another one than the one that ended.
   */
  const goLive = async () => {
    const camera = await studio.open();
    if (camera === null) return;
    const callbacks = {
      onSuccess: (started: MarketBroadcastResponse) => {
        if (started.broadcast?.status === "LIVE" && started.viewer.is_broadcaster) {
          studio.begin(started.broadcast.id);
        }
      },
      // The ended state shows no preview: a camera that was asked for in vain is let go.
      onError: () => {
        if (studio.getState().phase === "over") studio.release();
      },
    };
    if (viewer.can_start_as_admin) {
      startAsAdmin.mutate({ marketId }, callbacks);
    } else if (viewer.can_start && broadcast !== null) {
      startOwn.mutate({ marketId, broadcastId: broadcast.id }, callbacks);
    }
  };

  if (view === "proposer") {
    return frame(
      <>
        <Question text={market.data?.question} />
        <ProposerBroadcast
          marketId={marketId}
          answer={answer}
          onSetUp={told === "setup" ? () => setHeld(false) : undefined}
        />
      </>,
    );
  }

  if (view === "setup") {
    const hasPicture = state.camera !== null && pictured === state.camera.stream;
    return frame(
      <SettingUp studio={studio}>
        <Question text={market.data?.question} />
        <PreviewOrRefusal
          state={state}
          onPicture={setPictured}
          onResize={(width, height) => studio.reshaped(width, height)}
        />
        <StartError error={startError} />
        <Button
          loading={startPending}
          disabled={!canStart || !hasPicture}
          onClick={() => void goLive()}
        >
          Go live
        </Button>
        <div className="flex flex-col gap-2 type-body-sm text-muted-foreground">
          <p>Viewers see you a few seconds later. No picture and no sound is kept after it ends.</p>
          <p>
            It ends by itself after {minutesOf(config.max_minutes)}, or{" "}
            {secondsOf(config.stall_seconds)} after the last picture arrived.
          </p>
        </div>
      </SettingUp>,
    );
  }

  if (view === "live" && broadcast !== null) {
    return frame(
      <>
        <div className="flex flex-col gap-1">
          <div className="flex items-center gap-2">
            <LiveBadge />
            <h2 className="type-h2">You are live</h2>
          </div>
          {broadcast.started_at !== null && <Since startedAt={broadcast.started_at} />}
        </div>
        <PreviewOrRefusal
          state={state}
          onPicture={setPictured}
          onResize={(width, height) => studio.reshaped(width, height)}
        />
        <Banner>Keep this page open and in front. The stream pauses while it is hidden.</Banner>
        {state.slow && <Banner>Your connection is slow. Viewers may see pauses.</Banner>}
        {state.signedOut && <SignInBanner />}
        <EndStream marketId={marketId} broadcastId={broadcast.id} onEnded={() => studio.finish()} />
      </>,
    );
  }

  if (view === "paused" && broadcast !== null) {
    return frame(
      <>
        <div className="flex flex-col gap-2">
          <h2 className="type-h2">Your live stream is paused.</h2>
          <p className="type-body-sm text-muted-foreground">
            It ends by itself {secondsOf(config.stall_seconds)} after the last picture arrived.
          </p>
        </div>
        {state.refusal !== null && <RefusalBanner refusal={state.refusal} />}
        <div className="flex flex-col gap-3 md:flex-row">
          <Button loading={state.asking} onClick={() => void studio.resume(broadcast.id)}>
            Resume
          </Button>
          <EndStream
            marketId={marketId}
            broadcastId={broadcast.id}
            onEnded={() => studio.finish()}
          />
        </div>
      </>,
    );
  }

  // Ended. The answer names the reason when the stream that ended is the caller's own.
  const reason =
    broadcast?.status === "ENDED" && viewer.is_broadcaster
      ? endReasonSentence(broadcast.end_reason, config)
      : null;
  return frame(
    <>
      <div className="flex flex-col gap-2">
        <h2 className="type-h2">The live stream has ended.</h2>
        {state.refused !== null && <p className="type-body">{messageOf(state.refused)}</p>}
        {reason !== null && <p className="type-body">{reason}</p>}
        <p className="type-body-sm text-muted-foreground">No picture and no sound is kept.</p>
      </div>
      {state.refusal !== null && <RefusalBanner refusal={state.refusal} />}
      <StartError error={startError} />
      <div className="flex flex-col gap-3 md:flex-row">
        {viewer.can_start && (
          <Button loading={startPending || state.asking} onClick={() => void goLive()}>
            Go live again
          </Button>
        )}
        <Button variant="secondary" asChild>
          <Link href={`/markets/${marketId}`}>Back to the market</Link>
        </Button>
      </div>
    </>,
  );
}

/**
 * Setting up holds the camera for its preview: asked for when the state is shown, and let
 * go when it is left for anything but the live state.
 */
function SettingUp({ studio, children }: { studio: Studio; children: ReactNode }) {
  useEffect(() => {
    void studio.open();
    return () => studio.release();
  }, [studio]);

  return <>{children}</>;
}

/** The question of the market, on one line. */
function Question({ text }: { text: string | undefined }) {
  if (text === undefined) return null;
  return <p className="truncate type-body-sm text-muted-foreground">{text}</p>;
}

/** The preview, or the banner that names what the browser refused, where the preview would stand. */
function PreviewOrRefusal({
  state,
  onPicture,
  onResize,
}: {
  state: StudioState;
  onPicture: (stream: MediaStream) => void;
  onResize: (width: number, height: number) => void;
}) {
  if (state.refusal !== null) return <RefusalBanner refusal={state.refusal} />;
  if (state.camera === null) {
    // The browser is being asked: the place of the preview, without a picture.
    return <div data-slot="preview" className="aspect-video w-full rounded-lg bg-card" />;
  }
  const { stream } = state.camera;
  return (
    <Preview
      key={stream.id}
      stream={stream}
      onPicture={() => onPicture(stream)}
      onResize={onResize}
    />
  );
}

function RefusalBanner({ refusal }: { refusal: CaptureRefusal }) {
  return <Banner>{REFUSALS[refusal]}</Banner>;
}

/** A refusal of "Go live", above its button, as the API sent it (DESIGN.md §14). */
function StartError({ error }: { error: ApiError | null }) {
  return <SubmitError error={error} message={error === null ? null : messageOf(error)} />;
}

/** "Since 18:02": the time of day the stream started, in the timezone of `/config` (DESIGN.md §10). */
function Since({ startedAt }: { startedAt: string }) {
  const { timezone } = useConfig();
  return (
    <p className="type-body-sm text-muted-foreground">
      Since <time dateTime={startedAt}>{formatTime(startedAt, timezone)}</time>
    </p>
  );
}

/**
 * The sign-in banner: an upload was answered with the portal's 401. The studio keeps
 * recording and sending, and the broadcaster signs in again in another tab: a navigation
 * of this one would end the stream. The banner goes with the next upload that is answered.
 */
function SignInBanner() {
  return (
    <Banner
      action={
        <Button variant="secondary" size="compact" width="fit" asChild>
          <Link href="/" target="_blank" rel="noopener" prefetch={false}>
            Sign in again <span className="sr-only">(opens in another tab)</span>
          </Link>
        </Button>
      }
    >
      Your ETH sign-in has ended. Sign in again in another tab; the stream goes on from here.
    </Banner>
  );
}

/** Loading: the shape of the preview and of the button. */
function StudioSkeleton() {
  return (
    <>
      <Skeleton className="aspect-video w-full rounded-lg" />
      <Skeleton className="h-12 w-full md:w-28" />
    </>
  );
}
