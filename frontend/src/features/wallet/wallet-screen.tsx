"use client";

import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";
import { toast } from "sonner";

import { Banner, ErrorBanner } from "@/components/feedback";
import { Screen } from "@/components/shell";
import { Button } from "@/components/ui/button";
import { useWelcomeOpen } from "@/features/session";
import {
  issueVoucherMutation,
  redeemCodeMutation,
  replaceVoucherCodeMutation,
} from "@/lib/api/mutations";
import { ledgerQuery, meQuery, vouchersQuery } from "@/lib/api/queries";
import type { RedeemCodeResponse, VoucherItem } from "@/lib/api/schema";
import { useMe } from "@/lib/api/session";
import { formatTokens } from "@/lib/format";

import { type Arrival, BalanceCard, BalanceCardSkeleton } from "./balance-card";
import { RedeemCodeSheet } from "./redeem-code-sheet";
import { ActivitySection, VouchersSection } from "./sections";
import { useCodeFromAddress } from "./use-code-from-address";
import { VoucherQrSheet } from "./voucher-qr-sheet";
import { VoucherSheet } from "./voucher-sheet";
import { useWrite } from "./write";

/* The three writes of the Wallet, by the key each is told apart with in the cache. */
const ISSUE_VOUCHER = ["wallet", "issue-voucher"] as const;
const VOUCHER_CODE = ["wallet", "voucher-code"] as const;
const REDEEM_CODE = ["wallet", "redeem-code"] as const;

type Sheet = "voucher" | "qr" | "redeem";

/**
 * Wallet (`/wallet`): DESIGN.md §13. Top to bottom: the balance card, "Get a voucher" and
 * "Redeem code", the vouchers, the activity.
 *
 * Every figure is a field of the API, shown as sent, and every decision is the server's:
 * "Get a voucher" follows `can_get_voucher`, a product follows `affordable`, and nothing
 * here looks at the balance. The three writes live here, above their sheets, so that a
 * sheet that is closed, opened again or turned into a dialog finds its request as it left
 * it. A voucher code is held in the answer of its write while its view is open and is
 * written to no storage; closing the view forgets it.
 */
export function WalletScreen() {
  const me = useMe();
  // Monthly tokens and refunds are booked lazily, so the balance is read again whenever
  // the Wallet opens (DESIGN.md §14, "Live data").
  const meRead = useQuery(meQuery);
  const vouchers = useQuery(vouchersQuery);
  const ledger = useInfiniteQuery(ledgerQuery);

  const cardCode = useCodeFromAddress();
  const welcomeOpen = useWelcomeOpen();
  const [sheet, setSheet] = useState<Sheet | null>(cardCode === null ? null : "redeem");
  /** Counts the openings of the voucher sheet: each starts afresh, at the list. */
  const [opening, setOpening] = useState(0);
  const [qrVoucherId, setQrVoucherId] = useState<string | null>(null);
  const [arrival, setArrival] = useState<Arrival | null>(null);
  const hintId = useId();

  const closeSheet = () => setSheet(null);

  // Each write with what it would have changed: after a request of it that got no answer,
  // those reads have to answer before it may be sent again (DESIGN.md §14, "Other writes
  // without an answer"). A fresh voucher code changes nothing that is read.
  const issue = useWrite(issueVoucherMutation, {
    key: ISSUE_VOUCHER,
    reads: [vouchers, meRead],
    // An issue that got no answer closes its sheet at once; the banner below says the rest.
    onNoAnswer: closeSheet,
  });
  const redeem = useWrite(redeemCodeMutation, { key: REDEEM_CODE, reads: [meRead, ledger] });
  const code = useWrite(replaceVoucherCodeMutation, { key: VOUCHER_CODE });

  // One banner for the reads of the screen: a list that could not be read at all, or a
  // read that has to answer before another voucher may be issued.
  const unread = [vouchers, ledger].filter((read) => read.data === undefined && read.error);
  const readError = issue.rereadError ?? unread[0]?.error ?? null;
  const readAgain = () => {
    const asked = new Set([...unread, ...(issue.rereadError === null ? [] : [vouchers, meRead])]);
    for (const read of asked) void read.refetch();
  };

  // A first visit shows each part as a skeleton of itself until its read has answered;
  // after that, what was read before is on screen at once and is replaced as it changes.
  const firstRead = vouchers.isPending || ledger.isPending;
  const balanceLoading = firstRead && meRead.isFetching && !meRead.isFetchedAfterMount;

  const openSheet = (next: Sheet) => {
    issue.dismiss();
    setSheet(next);
  };

  const showQr = (voucher: VoucherItem) => {
    // Every tap asks for another code; the sheet is about the voucher that was asked for,
    // and shows the code once it is there.
    if (!code.send(voucher.id)) return;
    setQrVoucherId(voucher.id);
    openSheet("qr");
  };

  const redeemed = (answer: RedeemCodeResponse, before: string) => {
    closeSheet();
    setArrival((last) => ({ id: (last?.id ?? 0) + 1, before }));
    toast(`+${formatTokens(answer.value_cr)}`);
  };

  return (
    <Screen top="main" title="Wallet" width="narrow">
      {balanceLoading ? <BalanceCardSkeleton /> : <BalanceCard me={me} arrival={arrival} />}

      <div className="flex flex-col gap-2">
        {/* Side by side; one below the other where two labels do not fit, as with large
            text or at 200% zoom. */}
        <div className="grid grid-cols-[repeat(auto-fit,minmax(9rem,1fr))] gap-3">
          <Button
            width="full"
            disabled={!me.can_get_voucher || issue.blocked}
            aria-describedby={me.can_get_voucher ? undefined : hintId}
            onClick={() => {
              issue.forget();
              setOpening((count) => count + 1);
              openSheet("voucher");
            }}
          >
            Get a voucher
          </Button>
          <Button variant="secondary" width="full" onClick={() => openSheet("redeem")}>
            Redeem code
          </Button>
        </div>
        {!me.can_get_voucher && (
          <p id={hintId} className="type-body-sm text-muted-foreground">
            You do not have enough tokens for a voucher yet.
          </p>
        )}
      </div>

      {issue.unanswered !== null && (
        <Banner>
          We did not get an answer. If your voucher was issued, it is in the list below.
        </Banner>
      )}
      {readError !== null && <ErrorBanner error={readError} onRetry={readAgain} />}

      <VouchersSection
        vouchers={vouchers}
        onShowQr={showQr}
        askingFor={code.sending ? qrVoucherId : null}
        disabled={code.blocked}
      />
      <ActivitySection ledger={ledger} />

      <VoucherSheet
        key={opening}
        open={sheet === "voucher"}
        onClose={() => {
          closeSheet();
          issue.forget();
        }}
        issue={issue}
      />
      <VoucherQrSheet
        open={sheet === "qr"}
        onClose={() => {
          closeSheet();
          code.forget();
        }}
        voucher={vouchers.data?.items.find((voucher) => voucher.id === qrVoucherId)}
        code={code}
      />
      <RedeemCodeSheet
        // A sheet the route opens by itself waits until the welcome dialog is closed.
        open={sheet === "redeem" && !welcomeOpen}
        onClose={closeSheet}
        initialCode={cardCode}
        redeem={redeem}
        onRedeemed={(answer) => redeemed(answer, me.balance_cr)}
      />
    </Screen>
  );
}
