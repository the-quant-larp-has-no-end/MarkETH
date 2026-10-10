"use client";

import NextLink, { useLinkStatus } from "next/link";
import { type ComponentProps, useEffect } from "react";

import { useShellStore } from "./shell-store";

/** Tells the shell while the link it sits in is waiting for its route. */
function Pending() {
  const { pending } = useLinkStatus();
  const store = useShellStore();

  useEffect(() => {
    if (!pending) return;
    return store.beginNavigation();
  }, [pending, store]);

  return null;
}

/**
 * The link of the app: `next/link` with the same props, which also drives the route
 * progress line of the top bar (DESIGN.md §3, the Polybahn loader). Every link inside
 * the app is this one; the lint rules do not let `next/link` in anywhere else.
 */
export function Link({ children, ...props }: ComponentProps<typeof NextLink>) {
  return (
    <NextLink {...props}>
      {children}
      <Pending />
    </NextLink>
  );
}
