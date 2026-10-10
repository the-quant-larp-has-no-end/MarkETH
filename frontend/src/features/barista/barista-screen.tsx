"use client";

import { useEffect, useState } from "react";

import { Banner, RetryCountdown } from "@/components/feedback";
import { QrScanner } from "@/components/qr";
import { NotFoundScreen, Screen, useHideTabBar } from "@/components/shell";
import { type ApiError, messageOf } from "@/lib/api/errors";
import { redeemVoucherMutation } from "@/lib/api/mutations";
import { useMe } from "@/lib/api/session";
import { useSubmit } from "@/lib/api/submit";
import { BARISTA_RESULT_MS } from "@/lib/design";
import { useRetryAfter } from "@/lib/hooks";

import { createScanMemory } from "./scan-memory";
import { ScanResult, type Verdict, verdictOf } from "./scan-result";
import { TypeCodeSheet } from "./type-code-sheet";

/** Tells the one write of this screen apart in the cache. */
const REDEEM_VOUCHER = ["barista", "redeem-voucher"] as const;

/**
 * A QR that was sent is not sent again while it stays in front of the camera: a voucher
 * held to the scanner a moment too long is one scan, not two. It counts as a scan of its
 * own once the scanner has watched for this long without reading it.
 */
const SAME_QR_MS = 2000;
/** While such a QR stays in sight, the scanner looks again this often. */
const LOOK_AGAIN_MS = 500;

/* DESIGN.md §13: a short vibration, one pulse for accepted, two for rejected and one long
   pulse for a voucher that was redeemed a moment ago. */
const ACCEPTED_PULSE = 200;
const REJECTED_PULSES = [120, 80, 120];
const REPEAT_PULSE = 600;

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
 * 2.5 s, with a vibration, and the scanner returns. The API's answer says which result it
 * is: the scan that redeemed the voucher (green), a voucher this barista redeemed a moment
 * ago and scanned again (`repeat`, neither green nor red), or a voucher that is not valid
 * (red). A failure that is no verdict on the voucher shows the banner of DESIGN.md §14
 * over the scanner, never the red screen, and the barista scans again: nothing is sent a
 * second time by itself.
 *
 * One voucher in front of the camera is one request, however long it is held there
 * (`scan-memory.ts`). When it has been away and comes back, that is a scan, and the API
 * says what it is worth.
 */
function VoucherScanner() {
  // Like every write of the app it is tried at once or fails at once: a scan that waited
  // for the network would redeem a voucher later, when nobody stands at the counter. One
  // scan is one request: `mutate` asks the cache, not the last render, whether one is out.
  const redeem = useSubmit({ ...redeemVoucherMutation, mutationKey: REDEEM_VOUCHER });
  const [verdict, setVerdict] = useState<Verdict | null>(null);
  const [failure, setFailure] = useState<ApiError | null>(null);
  const [typing, setTyping] = useState(false);
  const [typed, setTyped] = useState("");
  const [live, setLive] = useState(false);
  const [lookingAway, setLookingAway] = useState(false);
  /** What the scanner has sent and has not lost sight of since. */
  const [memory] = useState(() => createScanMemory(SAME_QR_MS));
  const wait = useRetryAfter(failure);

  const checking = redeem.isPending;
  // Something stands in front of the camera view, or nothing may be sent: nothing is read.
  const covered = checking || verdict !== null || typing || wait > 0;
  const paused = covered || lookingAway;

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

  // A QR is out of sight only for as long as the scanner could have seen it: the time a
  // result, a check or the typing sheet covers the view does not count.
  useEffect(() => {
    memory.watching(performance.now(), !covered);
  }, [memory, covered]);

  const closeTyping = () => setTyping(false);

  /** Sends one code to the API. Returns false when nothing can be sent right now. */
  const check = (code: string, onRedeemed?: () => void): boolean => {
    if (verdict !== null || wait > 0) return false;
    const sent = redeem.mutate(code, {
      onSuccess: (voucher) => {
        // Nothing stands in front of an answer: a sheet opened meanwhile makes room.
        closeTyping();
        const said = verdictOf(voucher);
        vibrate(said.kind === "repeat" ? REPEAT_PULSE : ACCEPTED_PULSE);
        setVerdict(said);
        onRedeemed?.();
      },
      onError: (error) => {
        closeTyping();
        if (!isRejection(error)) return setFailure(error);
        vibrate(REJECTED_PULSES);
        setVerdict({ kind: "rejected" });
      },
    });
    // The banner of the scan before makes room for the answer of this one.
    if (sent) setFailure(null);
    return sent;
  };

  const scanned = (text: string) => {
    const now = performance.now();
    if (memory.holds(text, now)) {
      // A QR that was sent and is still there: look away, then look again.
      setLookingAway(true);
      return;
    }
    if (check(text)) memory.note(text, now);
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
