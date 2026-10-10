"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { Banner, RetryCountdown } from "@/components/feedback";
import { QrScanner } from "@/components/qr";
import { NotFoundScreen, Screen, useHideTabBar } from "@/components/shell";
import { type ApiError, messageOf } from "@/lib/api/errors";
import { redeemVoucherMutation } from "@/lib/api/mutations";
import { useMe } from "@/lib/api/session";
import { BARISTA_RESULT_MS } from "@/lib/design";
import { useRetryAfter } from "@/lib/hooks";

import { ScanResult, type Verdict } from "./scan-result";
import { TypeCodeSheet } from "./type-code-sheet";

/** Tells the one write of this screen apart in the cache. */
const REDEEM_VOUCHER = ["barista", "redeem-voucher"] as const;

/**
 * A QR the scanner has just had an answer for is not sent again while it stays in front
 * of the camera: a voucher held to the scanner a moment too long would otherwise be
 * answered "accepted" and then "not valid". It counts as a scan of its own once it has
 * been out of sight for this long.
 */
const SAME_QR_MS = 2000;
/** While such a QR stays in sight, the scanner looks again this often. */
const LOOK_AGAIN_MS = 500;

/* DESIGN.md §13: a short vibration, one pulse for accepted and two for rejected. */
const ACCEPTED_PULSE = 200;
const REJECTED_PULSES = [120, 80, 120];

function vibrate(pattern: number | number[]): void {
  // Not every phone can. The screen says the same thing either way.
  if (typeof navigator.vibrate === "function") navigator.vibrate(pattern);
}

/**
 * The API's verdict that a voucher is not valid: `CODE_INVALID` for a voucher that cannot
 * be redeemed, `VALIDATION_ERROR` for a scan that cannot be a voucher code. Every other
 * failure says nothing about the voucher.
 */
function isRejection(error: ApiError): boolean {
  return error.code === "CODE_INVALID" || error.code === "VALIDATION_ERROR";
}

/**
 * Barista (`/barista`): DESIGN.md §13. For roles BARISTA and ADMIN; anyone else sees the
 * "not found" screen. The check here is for the look of it only: the API decides who may
 * redeem a voucher.
 */
export function BaristaScreen() {
  const { role } = useMe();
  if (role !== "BARISTA" && role !== "ADMIN") return <NotFoundScreen />;

  return <VoucherScanner />;
}

/**
 * The scanner, built to be used at arm's length, fast, by someone holding a cup.
 *
 * A scan, or a typed voucher code, is sent as `code` to `POST /barista/vouchers/redeem`.
 * The scanner pauses until the answer is there; then the result fills the screen for
 * 2.5 s, with a vibration, and the scanner returns. A failure that is no verdict on the
 * voucher shows the banner of DESIGN.md §14 over the scanner, never the red screen, and
 * the barista scans again: nothing is sent a second time by itself.
 */
function VoucherScanner() {
  const client = useQueryClient();
  // Like every write of the app it is tried at once or fails at once: a scan that waited
  // for the network would redeem a voucher later, when nobody stands at the counter.
  const redeem = useMutation({ ...redeemVoucherMutation, mutationKey: REDEEM_VOUCHER });
  const [verdict, setVerdict] = useState<Verdict | null>(null);
  const [failure, setFailure] = useState<ApiError | null>(null);
  const [typing, setTyping] = useState(false);
  const [typed, setTyped] = useState("");
  const [live, setLive] = useState(false);
  const [lookingAway, setLookingAway] = useState(false);
  /** What the scanner last had an answer for, and when it was last in sight. */
  const lastScan = useRef<{ text: string; seenAt: number } | null>(null);
  const wait = useRetryAfter(failure);

  const checking = redeem.isPending;
  const paused = checking || verdict !== null || typing || lookingAway || wait > 0;

  useHideTabBar(live);

  // The result shows for 2.5 s, then the scanner returns.
  useEffect(() => {
    if (verdict === null) return;
    const timer = window.setTimeout(() => setVerdict(null), BARISTA_RESULT_MS);
    return () => window.clearTimeout(timer);
  }, [verdict]);

  useEffect(() => {
    if (!lookingAway) return;
    const timer = window.setTimeout(() => setLookingAway(false), LOOK_AGAIN_MS);
    return () => window.clearTimeout(timer);
  }, [lookingAway]);

  // When the scanner looks again, the QR it has just had an answer for may still be
  // there: its time out of sight is counted from now.
  useEffect(() => {
    if (!paused && lastScan.current !== null) lastScan.current.seenAt = Date.now();
  }, [paused]);

  const closeTyping = () => setTyping(false);

  /** Sends one code to the API. Returns false when nothing can be sent right now. */
  const check = (code: string, onAccepted?: () => void): boolean => {
    if (verdict !== null || wait > 0) return false;
    // Asked of the cache, not of the last render: one scan is one request.
    if (client.isMutating({ mutationKey: REDEEM_VOUCHER }) > 0) return false;
    setFailure(null);
    redeem.mutate(code, {
      onSuccess: (voucher) => {
        // Nothing stands in front of an answer: a sheet opened meanwhile makes room.
        closeTyping();
        vibrate(ACCEPTED_PULSE);
        setVerdict({ accepted: true, voucher });
        onAccepted?.();
      },
      onError: (error) => {
        closeTyping();
        if (!isRejection(error)) return setFailure(error);
        vibrate(REJECTED_PULSES);
        setVerdict({ accepted: false });
      },
    });
    return true;
  };

  const scanned = (text: string) => {
    const now = Date.now();
    const last = lastScan.current;
    if (last !== null && last.text === text && now - last.seenAt < SAME_QR_MS) {
      // Still the QR that was sent a moment ago: look away, then look again.
      last.seenAt = now;
      setLookingAway(true);
      return;
    }
    if (check(text)) lastScan.current = { text, seenAt: now };
  };

  const notice = checking ? (
    <Banner>Checking the voucher.</Banner>
  ) : failure !== null ? (
    <Banner
      tone="error"
      action={wait > 0 ? <RetryCountdown error={failure} className="text-no-text" /> : undefined}
    >
      {messageOf(failure)}
    </Banner>
  ) : null;

  return (
    // With the tab bar out of the way, the scanner takes its room too.
    <Screen top="main" title="Scan" bleed className={live ? "pb-0" : undefined}>
      <div className="relative mx-auto flex min-h-0 w-full flex-1 flex-col md:max-w-[45rem]">
        {notice !== null && (
          // Over the camera view while there is one; above the scanner's own message
          // while there is none.
          <div
            data-slot="scan-notice"
            className={live ? "absolute inset-x-8 top-8 z-10" : "px-4 pt-4"}
          >
            {notice}
          </div>
        )}
        <QrScanner
          onScan={scanned}
          paused={paused}
          alternativeLabel="Type the voucher code"
          onAlternative={() => setTyping(true)}
          onLiveChange={setLive}
          // The view fills the screen below the top bar (DESIGN.md §13).
          fill
          className="p-4 pb-[max(1rem,env(safe-area-inset-bottom))]"
        />
      </div>

      <TypeCodeSheet
        open={typing}
        onOpenChange={(open) => {
          if (!open) closeTyping();
        }}
        code={typed}
        onCodeChange={setTyped}
        disabled={checking || wait > 0}
        onSubmit={() => {
          closeTyping();
          // A code that was rejected stays in the field, to be corrected.
          check(typed, () => setTyped(""));
        }}
      />
      <ScanResult verdict={verdict} />
    </Screen>
  );
}
