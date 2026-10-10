"use client";

import { useQuery } from "@tanstack/react-query";

import { configQuery, meQuery } from "./queries";
import type { MeResponse, PublicConfigResponse } from "./schema";

/**
 * The tunables of the product, from `GET /config`. Every limit, preset, length, interval
 * and list a screen shows or uses comes from here or from the response in hand.
 *
 * Start-up loads it before any screen renders, so there is nothing to wait for and no
 * "not loaded" case to handle. Outside start-up's gate it throws.
 */
export function useConfig(): PublicConfigResponse {
  const { data } = useQuery(configQuery);
  if (data === undefined) {
    throw new Error("useConfig() was called outside the app's start-up gate.");
  }
  return data;
}

/**
 * The person using the app, from `GET /me`: profile, role and balance. Everybody past
 * start-up has a profile, so this is never null and no screen has a signed-out state.
 *
 * It follows the cache: a write that changes the balance updates it, and so does the
 * window getting the focus back. It does not ask the API again just because a component
 * mounted; a screen that must show a fresh balance when it opens (the Wallet) uses
 * `useQuery(meQuery)` itself.
 */
export function useMe(): MeResponse {
  const { data } = useQuery({ ...meQuery, refetchOnMount: false });
  if (data === undefined) {
    throw new Error("useMe() was called outside the app's start-up gate.");
  }
  return data;
}
