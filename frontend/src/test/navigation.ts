import { createElement, type MouseEvent, type ReactNode, useSyncExternalStore } from "react";
import { vi } from "vitest";

/**
 * The router, for tests. `src/test/setup.ts` puts this in place of `next/navigation` and
 * `next/link`, so a component under test navigates in memory and the test can look:
 *
 *     navigation.go("/wallet?code=ABCD-EFGH-JKMN");   // the address the test starts at
 *     render(...);
 *     await user.click(screen.getByRole("link", { name: "Portfolio" }));
 *     expect(navigation.pathname).toBe("/portfolio");
 *     expect(navigation.router.replace).toHaveBeenCalledWith("/wallet");
 *
 * `usePathname`, `useSearchParams` and `useParams` follow it, and components render again
 * when the address changes, as they do in the app.
 */

const ORIGIN = "http://localhost:3000";

type Entry = { url: URL; search: URLSearchParams };

function entryFor(href: string, base: URL): Entry {
  const url = new URL(href, base);
  return { url, search: new URLSearchParams(url.search) };
}

let entries: Entry[] = [entryFor("/", new URL(ORIGIN))];
let index = 0;
let params: Record<string, string> = {};
const listeners = new Set<() => void>();

function current(): Entry {
  return entries[index]!;
}

function changed(): void {
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

const router = {
  push: vi.fn((href: string) => {
    entries = [...entries.slice(0, index + 1), entryFor(href, current().url)];
    index = entries.length - 1;
    changed();
  }),
  replace: vi.fn((href: string) => {
    entries = [...entries.slice(0, index), entryFor(href, current().url)];
    changed();
  }),
  back: vi.fn(() => {
    if (index === 0) return;
    index -= 1;
    changed();
  }),
  forward: vi.fn(() => {
    if (index >= entries.length - 1) return;
    index += 1;
    changed();
  }),
  refresh: vi.fn(),
  prefetch: vi.fn(),
};

export const navigation = {
  router,

  /** Where the test starts: sets the address without a history before it. */
  go(href: string, routeParams: Record<string, string> = {}): void {
    entries = [entryFor(href, new URL(ORIGIN))];
    index = 0;
    params = routeParams;
    changed();
  },

  get pathname(): string {
    return current().url.pathname;
  },

  /** The query string without its `?`, e.g. `sort=trades&order=asc`. */
  get search(): string {
    return current().search.toString();
  },

  /** Pathname and query together. */
  get href(): string {
    const { url } = current();
    return url.pathname + url.search;
  },

  /** How many entries lie behind the current one. */
  get depth(): number {
    return index;
  },
};

/** Run before every test (`src/test/setup.ts`). */
export function resetNavigation(): void {
  navigation.go("/");
  for (const spy of Object.values(router)) spy.mockClear();
}

/** What stands in for `next/navigation`. */
export const nextNavigationModule = {
  useRouter: () => router,
  usePathname: () =>
    useSyncExternalStore(
      subscribe,
      () => current().url.pathname,
      () => current().url.pathname,
    ),
  useSearchParams: () =>
    useSyncExternalStore(
      subscribe,
      () => current().search,
      () => current().search,
    ),
  useParams: () => params,
  notFound: () => {
    throw new Error("notFound() was called");
  },
  redirect: (href: string) => {
    throw new Error(`redirect(${href}) was called`);
  },
};

type LinkProps = {
  href: string;
  replace?: boolean;
  children?: ReactNode;
  onClick?: (event: MouseEvent<HTMLAnchorElement>) => void;
  // What only the real link understands.
  prefetch?: unknown;
  scroll?: unknown;
  [attribute: string]: unknown;
};

/** What stands in for `next/link`: an anchor that navigates in memory. */
function Link({
  href,
  replace,
  onClick,
  prefetch: _prefetch,
  scroll: _scroll,
  ...rest
}: LinkProps) {
  return createElement("a", {
    ...rest,
    href,
    onClick: (event: MouseEvent<HTMLAnchorElement>) => {
      onClick?.(event);
      if (event.defaultPrevented) return;
      event.preventDefault();
      if (replace) router.replace(href);
      else router.push(href);
    },
  });
}

export const nextLinkModule = {
  default: Link,
  useLinkStatus: () => ({ pending: false }),
};
