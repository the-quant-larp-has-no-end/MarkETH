"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useEffectEvent, useRef, useState } from "react";

import { Banner } from "@/components/feedback";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { marketBroadcastQuery } from "@/lib/api/queries";
import type { MarketBroadcastResponse } from "@/lib/api/schema";
import { useConfig } from "@/lib/api/session";
import { cn } from "@/lib/utils";

import { playLiveStream } from "./player-engine";

/**
 * What the box shows in place of the picture, or with it (DESIGN.md §12, "Video box").
 *
 * - `connecting`: until the first frame, a skeleton as large as the box.
 * - `playing`: the picture.
 * - `waiting`: no picture arrived for `wait_notice_ms`, or the player failed: the waiting
 *   line stands over the last frame while the API is asked and the player is built again.
 * - `held`: the browser waits for its viewer to start the picture with its own control.
 * - `unplayable`: this browser plays a live stream in no way.
 * - `failed`: the API could not say what became of the live stream, so no player is built
 *   until the viewer asks again.
 */
type Phase = "connecting" | "playing" | "waiting" | "held" | "unplayable" | "failed";

export type BroadcastPlayerProps = {
  /** The market whose live stream plays: what is asked about when the picture stays away. */
  marketId: string;
  /** `playlist_url` of the live stream: a path on the app's own origin. */
  playlistUrl: string;
  /** The question of the market: the box is named "Live stream: {question}". */
  question?: string;
  className?: string;
};

/** The live stream of the answer is the one this box plays, and its viewer may watch it. */
function stillPlayable(answer: MarketBroadcastResponse, playlistUrl: string): boolean {
  return (
    answer.broadcast?.status === "LIVE" &&
    answer.broadcast.playlist_url === playlistUrl &&
    answer.viewer.can_watch
  );
}

/**
 * The video box of a live stream (DESIGN.md §12): 16:9, as wide as what it stands in, the
 * picture fitted into it whole, played by the browser's own video element with the
 * browser's own controls. It is mounted for a tap ("Watch live") and plays from then on;
 * taking it away ends every request for video.
 *
 * **A player that fails, or shows no picture for `wait_notice_ms`,** asks the API what
 * became of the live stream (DESIGN.md §13). While the answer is this live stream, on and
 * open to this viewer, another player is built after `player_retry_ms`, as often as it
 * takes. Every other answer is the section's to show: it takes this box away. A picture
 * that comes back by itself meanwhile is kept, and no player is built over it. A viewer
 * who pauses the picture is not waited for.
 *
 * Both paces are fields of `/config` `broadcast`. Nothing of the picture is kept anywhere.
 */
export function BroadcastPlayer({
  marketId,
  playlistUrl,
  question,
  className,
}: BroadcastPlayerProps) {
  const client = useQueryClient();
  const { wait_notice_ms: waitNoticeMs, player_retry_ms: playerRetryMs } = useConfig().broadcast;

  // The element arrives through a callback ref, so that the player starts when it does.
  const [video, setVideo] = useState<HTMLVideoElement | null>(null);
  const [phase, setPhase] = useState<Phase>("connecting");
  /** Counts the players of this box: another number is another player. */
  const [attempt, setAttempt] = useState(0);

  /** The trouble that is being looked into: its number, and the player that waits to be built. */
  const trouble = useRef<{ count: number; timer: number | undefined }>({
    count: 0,
    timer: undefined,
  });

  /** Nothing is wrong any more: an answer that is on its way belongs to nobody. */
  const settle = () => {
    trouble.current.count += 1;
    window.clearTimeout(trouble.current.timer);
  };

  /** Asks what became of the live stream. The answer decides; another player waits `wait` ms. */
  const ask = async (wait: number) => {
    settle();
    const mine = trouble.current.count;
    let answer: MarketBroadcastResponse;
    try {
      // The read of the section, asked once more: an answer that is on its way (the
      // stream's event may have asked) answers this too, and the section shows what comes.
      answer = await client.fetchQuery(marketBroadcastQuery(marketId));
    } catch {
      if (mine === trouble.current.count) setPhase("failed");
      return;
    }
    // Anything else is the section's to show, and it takes this box away.
    if (mine !== trouble.current.count || !stillPlayable(answer, playlistUrl)) return;
    trouble.current.timer = window.setTimeout(() => {
      setPhase((shown) => (shown === "failed" ? "connecting" : shown));
      setAttempt((players) => players + 1);
    }, wait);
  };

  const onPicture = useEffectEvent(() => {
    settle();
    setPhase("playing");
  });
  const onTrouble = useEffectEvent(() => {
    setPhase("waiting");
    void ask(playerRetryMs);
  });
  const onHeld = useEffectEvent(() => setPhase("held"));
  const onUnplayable = useEffectEvent(() => {
    settle();
    setPhase("unplayable");
  });

  useEffect(() => {
    if (video === null) return;

    // The picture moves while the position of the playback does.
    let position = video.currentTime;
    let silence: number | undefined;
    const rest = () => window.clearTimeout(silence);
    const listen = () => {
      rest();
      silence = window.setTimeout(onTrouble, waitNoticeMs);
    };
    const moved = () => {
      if (video.currentTime === position) return;
      position = video.currentTime;
      listen();
      onPicture();
    };

    video.addEventListener("timeupdate", moved);
    // A picture its viewer paused is not one that stays away.
    video.addEventListener("pause", rest);
    video.addEventListener("play", listen);
    listen();

    const stop = playLiveStream(video, playlistUrl, {
      onUnplayable: () => {
        rest();
        onUnplayable();
      },
      onFailed: () => {
        rest();
        onTrouble();
      },
      onHeld: () => {
        rest();
        onHeld();
      },
    });

    return () => {
      rest();
      video.removeEventListener("timeupdate", moved);
      video.removeEventListener("pause", rest);
      video.removeEventListener("play", listen);
      stop();
    };
  }, [video, playlistUrl, attempt, waitNoticeMs]);

  // Whoever leaves takes the question with them: no answer builds a player afterwards.
  useEffect(() => {
    const looking = trouble.current;
    return () => {
      looking.count += 1;
      window.clearTimeout(looking.timer);
    };
  }, []);

  const name = question === undefined ? "Live stream" : `Live stream: ${question}`;
  const hasElement = phase !== "unplayable" && phase !== "failed";

  return (
    <div
      data-slot="video-box"
      data-phase={phase}
      className={cn(
        "relative flex aspect-video w-full items-center justify-center overflow-hidden rounded-md bg-background",
        className,
      )}
    >
      {hasElement && (
        <video
          ref={setVideo}
          aria-label={name}
          controls
          playsInline
          preload="none"
          className="size-full object-contain"
        />
      )}

      {/* What is said about the picture without taking the focus (DESIGN.md §15). It lies
          over the picture and takes no tap: the browser's own controls stay reachable. */}
      <div
        role="status"
        className="pointer-events-none absolute inset-0 flex items-center justify-center p-4"
      >
        {phase === "connecting" && (
          <>
            <Skeleton className="absolute inset-0 rounded-none" />
            <span className="sr-only">Connecting to the live stream</span>
          </>
        )}
        {phase === "waiting" && (
          <p className="rounded-sm bg-card px-3 py-2 text-center type-body-sm text-foreground">
            Waiting for the live stream…
          </p>
        )}
      </div>

      {phase === "unplayable" && (
        <p className="p-4 text-center type-body-sm text-muted-foreground">
          This browser cannot play the live stream.
        </p>
      )}

      {phase === "failed" && (
        <Banner
          tone="error"
          className="m-4"
          action={
            <Button variant="secondary" size="compact" width="fit" onClick={() => void ask(0)}>
              Try again
            </Button>
          }
        >
          The live stream could not be played.
        </Banner>
      )}
    </div>
  );
}
