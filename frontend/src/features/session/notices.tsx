"use client";

import { type QueryClient, useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { toast } from "sonner";

import { TextLink } from "@/components/shell";
import { markNoticeSeenMutation } from "@/lib/api/mutations";
import type { NoticeItem } from "@/lib/api/schema";

/**
 * The notices this page has told its user, by the page's query client: one set per page
 * load, in memory. It is what keeps a notice from being told twice when marking it seen
 * failed and the next `/me` lists it again.
 */
const toldBy = new WeakMap<QueryClient, Set<string>>();

function toldIn(client: QueryClient): Set<string> {
  let told = toldBy.get(client);
  if (told === undefined) {
    told = new Set();
    toldBy.set(client, told);
  }
  return told;
}

/**
 * `kind` is a key, not copy: this is the sentence for it. Null for a kind this app has no
 * sentence for, which is then neither told nor marked seen.
 */
function sentenceOf(notice: NoticeItem): string | null {
  if (notice.kind !== "PROPOSAL_APPROVED") return null;
  return `Your market is open: ${notice.question}`;
}

/**
 * What the user is told once (`notices` of `GET /me`, oldest first): VIS approved the
 * market they proposed, and it is open. Each notice is shown as a toast, "Your market is
 * open: {question}", with the link "View" that leads to the market, and is then marked
 * seen with `POST /me/notices/{notice_id}/seen`, after which `/me` lists it no more.
 *
 * `/me` is read at start-up, on every navigation and when the window gets the focus back,
 * so a notice shows on whatever screen its user is looking at. A notice that was shown is
 * not shown a second time by this page, also when marking it seen got no answer: the mark
 * is not sent again by itself, and a later page tells the notice once more.
 *
 * It renders nothing itself: the toasts are sonner's.
 */
export function Notices({ notices }: { notices: readonly NoticeItem[] }) {
  const client = useQueryClient();
  // The one write no tap sends. Each notice is its own request.
  const { mutate: markSeen } = useMutation(markNoticeSeenMutation);

  useEffect(() => {
    const told = toldIn(client);
    for (const notice of notices) {
      if (told.has(notice.id)) continue;
      const sentence = sentenceOf(notice);
      if (sentence === null) continue;
      told.add(notice.id);
      toast(sentence, {
        id: notice.id,
        action: (
          <TextLink
            href={`/markets/${notice.market_id}`}
            onClick={() => toast.dismiss(notice.id)}
            // At the right end of the toast, before its cross, with a target 44 px wide.
            className="ml-auto min-w-11 shrink-0 justify-center"
          >
            View
          </TextLink>
        ),
      });
      markSeen(notice.id);
    }
  }, [notices, client, markSeen]);

  return null;
}
