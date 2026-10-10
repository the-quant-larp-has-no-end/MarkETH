"use client";

import { type QueryClient, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

const drafts = new WeakMap<QueryClient, Map<string, unknown>>();

/**
 * What a form holds before it is sent, kept by the page's query client so that it outlives
 * the form (DESIGN.md §12, "Form field"): whoever leaves a form to read the usage policy
 * and comes back finds what was typed. It is in memory and in no browser storage: a
 * reload forgets it.
 *
 * `name` says which form it is; a form that keeps nothing passes `null`. `empty` is what
 * the form starts from. Returns what is entered, `keep` to change it, and `forget`, which
 * the form calls once the server has taken what was entered.
 */
export function useDraft<Entered>(
  name: string | null,
  empty: Entered,
): [entered: Entered, keep: (next: Entered) => void, forget: () => void] {
  const client = useQueryClient();
  const [entered, setEntered] = useState<Entered>(() => {
    const kept = name === null ? undefined : drafts.get(client)?.get(name);
    return kept === undefined ? empty : (kept as Entered);
  });

  const keep = (next: Entered) => {
    setEntered(next);
    if (name === null) return;
    const ofClient = drafts.get(client) ?? new Map<string, unknown>();
    drafts.set(client, ofClient.set(name, next));
  };

  const forget = () => {
    if (name !== null) drafts.get(client)?.delete(name);
  };

  return [entered, keep, forget];
}
