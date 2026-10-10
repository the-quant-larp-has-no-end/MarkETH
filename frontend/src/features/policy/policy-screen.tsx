"use client";

import { BareTopBar, Screen } from "@/components/shell";

import { PolicyText } from "./policy-text";

/**
 * Usage policy (`/policy`, DESIGN.md §13): an inner screen with a back arrow and the
 * title "Usage policy", and the text in a column of at most 720 px. No tab is active.
 * There is no illustration, no campus line and no button: it is read, and left with the
 * back arrow.
 */
export function PolicyScreen() {
  return (
    <Screen top="inner" title="Usage policy" width="narrow">
      <PolicyText />
    </Screen>
  );
}

/**
 * The same page for a visitor who has no profile yet: the one route the profile set-up
 * does not stand in front of, because the policy is read before it is accepted. It
 * stands on the frame of the boot screen: the top bar with the logo and nothing on its
 * right, no tab bar, and the title as a heading above the text. The logo leads to the
 * profile set-up: with `fromSetup`, one step back to the set-up the visitor came from,
 * which stands at the address that was opened and holds what was typed.
 *
 * Start-up shows it in place of the route. It uses the tunables and nothing of a profile.
 */
export function PolicyForVisitor({ fromSetup = false }: { fromSetup?: boolean }) {
  return (
    <div data-slot="policy-visitor" className="flex min-h-dvh flex-col">
      <BareTopBar href="/" back={fromSetup} />
      <main className="mx-auto flex w-full flex-1 flex-col gap-6 px-4 pt-4 pb-6 md:max-w-[45rem]">
        <h1 className="type-h1">Usage policy</h1>
        <PolicyText />
      </main>
    </div>
  );
}
