"use client";

import NextLink, { useLinkStatus } from "next/link";
import { type ComponentProps, useEffect } from "react";

import { cn } from "@/lib/utils";

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

/**
 * A link that is text (DESIGN.md §12): inside a sentence, or a line of its own such as
 * "Usage policy". `--foreground`, underlined, in the text style of its line, with a
 * target 44 px tall. Green and red are never used for a link.
 *
 * With `newTab` it opens beside the page, which then keeps what was typed into it: the
 * usage policy from a form. Say so near the link for a screen reader (`aria-describedby`).
 */
export function TextLink({
  className,
  newTab = false,
  children,
  ...props
}: ComponentProps<typeof NextLink> & { newTab?: boolean }) {
  return (
    <Link
      {...(newTab ? { target: "_blank", rel: "noopener" } : {})}
      {...props}
      data-slot="text-link"
      className={cn(
        "inline-flex min-h-11 items-center text-foreground underline underline-offset-4",
        "transition-opacity duration-(--duration-fast) hover:opacity-90 active:opacity-90",
        className,
      )}
    >
      {children}
    </Link>
  );
}
