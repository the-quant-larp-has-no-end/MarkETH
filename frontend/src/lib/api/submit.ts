"use client";

import {
  type MutateOptions,
  type MutationObserverBaseResult,
  useIsMutating,
  useMutation,
  type UseMutationOptions,
  useQueryClient,
} from "@tanstack/react-query";
import { useId } from "react";

import type { ApiError } from "./errors";

/** What `useSubmit` returns: the result of `useMutation`, with a `mutate` that guards itself. */
export type Submit<TData, TVariables, TContext = unknown> = Omit<
  MutationObserverBaseResult<TData, ApiError, TVariables, TContext>,
  "mutate"
> & {
  /**
   * Sends one request and says so with true. While a request of this write is out it
   * sends nothing and returns false.
   */
  mutate: (
    variables: TVariables,
    callbacks?: MutateOptions<TData, ApiError, TVariables, TContext>,
  ) => boolean;
};

/**
 * `useMutation` for a write that a button or a form sends (DESIGN.md §14, "Buttons that
 * change state"): a second submit is stopped here, at the moment it is made.
 *
 *     const save = useSubmit(saveProfileMutation);
 *     ...
 *     save.mutate(body, { onSuccess: onSaved });   // false, and nothing sent, while one is out
 *     <Button type="submit" loading={save.isPending}>Save</Button>
 *
 * `isPending` of the last render is no guard: the cache tells a component that its request
 * went out a moment after it did, so two submits in that moment (Enter and a click, a
 * double tap that lands twice) would both pass a check of it. `mutate` asks the mutation
 * cache instead, which knows at once.
 *
 * The guard is this component's own unless the write names a `mutationKey`: then it holds
 * for every surface that sends that write, also one that was closed and opened again while
 * its request was out. `isPending` is true for as long as the guard holds.
 */
export function useSubmit<TData = unknown, TVariables = void, TContext = unknown>(
  options: UseMutationOptions<TData, ApiError, TVariables, TContext>,
): Submit<TData, TVariables, TContext> {
  const client = useQueryClient();
  const own = useId();
  const mutationKey = options.mutationKey ?? ["submit", own];
  const { mutateAsync: _unguarded, ...mutation } = useMutation({ ...options, mutationKey });
  const out = useIsMutating({ mutationKey, exact: true }) > 0;

  return {
    ...mutation,
    isPending: mutation.isPending || out,
    mutate(variables, callbacks) {
      if (client.isMutating({ mutationKey, exact: true }) > 0) return false;
      mutation.mutate(variables, callbacks);
      return true;
    },
  };
}
