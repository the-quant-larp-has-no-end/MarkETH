import { type QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  render,
  renderHook,
  type RenderHookOptions,
  type RenderOptions,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement, ReactNode } from "react";

import { ShellProvider } from "@/components/shell";
import { Toaster } from "@/components/ui/sonner";
import { keys } from "@/lib/api/keys";
import { createQueryClient } from "@/lib/api/query-client";
import type { MeResponse, PublicConfigResponse } from "@/lib/api/schema";

import * as fixtures from "./fixtures";

/** The app's query client, except that a failed read is not asked again: tests do not wait. */
export function createTestQueryClient(): QueryClient {
  const client = createQueryClient();
  const defaults = client.getDefaultOptions();
  client.setDefaultOptions({ ...defaults, queries: { ...defaults.queries, retry: false } });
  return client;
}

export type ProvidersOptions = {
  /**
   * What `useConfig()` returns. Defaults to `fixtures.config()`. Pass null to leave it
   * out of the cache, for a test of start-up itself.
   */
  config?: PublicConfigResponse | null;
  /** What `useMe()` returns. Defaults to `fixtures.me()`; null leaves it out. */
  me?: MeResponse | null;
  /** A client of your own, to look into its cache or to share it between renders. */
  client?: QueryClient;
};

function providers({ config, me, client = createTestQueryClient() }: ProvidersOptions) {
  if (config !== null) client.setQueryData(keys.config, config ?? fixtures.config());
  if (me !== null) client.setQueryData(keys.me, me ?? fixtures.me());

  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>
      <ShellProvider>{children}</ShellProvider>
      <Toaster />
    </QueryClientProvider>
  );
  return { client, wrapper };
}

/**
 * Renders a screen or a component as it stands inside the app, past start-up: a query
 * client, the tunables and the current user in its cache, the shell's state and the
 * toasts.
 *
 *     mockApi({ "GET /api/me/positions": json(200, portfolio()) });
 *     const { user } = renderWithProviders(<PortfolioScreen />, { me: me({ role: "ADMIN" }) });
 *     await user.click(await screen.findByRole("tab", { name: "History" }));
 *
 * A component that reads the API needs `mockApi` first. `useMe()` and `useConfig()` read
 * the cache and ask for nothing; a screen that calls `useQuery(meQuery)` itself does ask,
 * and needs a route for `GET /api/me`.
 */
export function renderWithProviders(
  ui: ReactElement,
  { config, me, client, ...options }: ProvidersOptions & Omit<RenderOptions, "wrapper"> = {},
) {
  const made = providers({ config, me, client });
  return {
    ...render(ui, { wrapper: made.wrapper, ...options }),
    client: made.client,
    user: userEvent.setup(),
  };
}

/** The same surroundings for a hook. */
export function renderHookWithProviders<Result, Props>(
  hook: (props: Props) => Result,
  {
    config,
    me,
    client,
    ...options
  }: ProvidersOptions & Omit<RenderHookOptions<Props>, "wrapper"> = {},
) {
  const made = providers({ config, me, client });
  return { ...renderHook(hook, { wrapper: made.wrapper, ...options }), client: made.client };
}
