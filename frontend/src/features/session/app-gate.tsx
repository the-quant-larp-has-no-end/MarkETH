"use client";

import { useQuery } from "@tanstack/react-query";
import { usePathname } from "next/navigation";
import { createContext, type ReactNode, useContext, useEffect, useState } from "react";

import { PolicyForVisitor } from "@/features/policy";
import { portalIdentity } from "@/lib/api/errors";
import { configQuery, meQuery } from "@/lib/api/queries";
import type { ProfileSavedResponse } from "@/lib/api/schema";
import { confirmPortalSession } from "@/lib/session-recovery";

import { BootScreen } from "./boot-screen";
import { NoticeScreen } from "./notice-screen";
import { Notices } from "./notices";
import { ProfileSetupScreen } from "./profile-setup-screen";
import { WelcomeDialog } from "./welcome-dialog";

const WelcomeContext = createContext(false);

/** The usage policy: a visitor without a profile reads it before accepting it. */
const POLICY_ROUTE = "/policy";

/**
 * True while the welcome dialog is open. A sheet a route would open by itself (the
 * redeem sheet of a code card) waits until this is false (DESIGN.md §13).
 */
export function useWelcomeOpen(): boolean {
  return useContext(WelcomeContext);
}

/**
 * What a visitor without a profile sees in place of the route they opened: the profile
 * set-up, on every route but one. `/policy` is the route the set-up does not stand in
 * front of (DESIGN.md §11): the usage policy is read before it is accepted. A visitor who
 * came to it from the set-up goes back there with the logo, to the address the set-up
 * stood at, so a code card's code is still in it.
 *
 * The address is read here and not in the gate, which is also what the server renders:
 * this is shown only once `/me` has answered, which happens in the browser.
 */
function WithoutProfile(props: Parameters<typeof ProfileSetupScreen>[0]) {
  const pathname = usePathname();
  // The gate stays while the routes change: it has seen the set-up or it has not.
  const [setupShown, setSetupShown] = useState(pathname !== POLICY_ROUTE);
  if (pathname !== POLICY_ROUTE && !setupShown) setSetupShown(true);

  if (pathname === POLICY_ROUTE) return <PolicyForVisitor fromSetup={setupShown} />;
  return <ProfileSetupScreen {...props} />;
}

/**
 * Start-up (DESIGN.md §13): what stands in front of every route until the app knows who
 * is looking. The portal has signed the visitor in before the page loaded; `/config` and
 * `/me` are asked together, and the answer of `/me` decides what is shown:
 *
 * | `/me` answers                   | Shown                                              |
 * | ------------------------------- | -------------------------------------------------- |
 * | the profile                     | the route that was opened (`children`)             |
 * | `PROFILE_REQUIRED`              | profile set-up, in place of the route; on          |
 * |                                 | `/policy` the usage policy, so that it can be read |
 * |                                 | before it is accepted                              |
 * | `FORBIDDEN`                     | the full-screen message with the API's text        |
 * | `UNAUTHENTICATED`               | the same, with "Try again"                         |
 * | the portal's 401                | the page reloads once; then "Your ETH sign-in has  |
 * |                                 | ended." with "Try again"                           |
 * | no answer, an error of the server | the full-screen message with "Try again"         |
 *
 * A `/config` that fails counts as the last row. It is the latest answer of `/me` that
 * counts, also later on: an account that is suspended while the app is open gets the
 * full-screen message with the next `/me`.
 *
 * Everything inside `children` may call `useMe()` and `useConfig()`: nobody is signed
 * out, and nobody past this gate is without a profile.
 *
 * `/me` also lists what its user has not been told yet (`notices`): each is shown once as
 * a toast over whatever route is open, and marked seen (`Notices`).
 */
export function AppGate({ children }: { children: ReactNode }) {
  const config = useQuery(configQuery);
  const me = useQuery(meQuery);
  const [welcome, setWelcome] = useState<{ saved: ProfileSavedResponse; open: boolean } | null>(
    null,
  );

  // A `/me` that answered went through the portal: its session works.
  useEffect(() => {
    if (me.isSuccess) confirmPortalSession();
  }, [me.isSuccess, me.dataUpdatedAt]);

  // One of the two calls met the portal's 401 and reloaded the page: it is on its way to
  // the sign-in, and there is nothing to say yet.
  const reloading = [config.error, me.error].some(
    (error) => error?.kind === "portal" && error.recovering,
  );
  if (reloading) return <BootScreen />;

  // What the API says about the caller comes first, whatever else is known.
  if (me.error?.code === "FORBIDDEN") return <NoticeScreen message={me.error.message} />;
  if (me.error?.code === "UNAUTHENTICATED") {
    return <NoticeScreen message={me.error.message} retry />;
  }

  // Every screen needs the tunables, the set-up screen included.
  if (config.data === undefined) {
    if (config.error !== null) return <NoticeScreen message={config.error.message} retry />;
    return <BootScreen />;
  }

  if (me.error?.code === "PROFILE_REQUIRED") {
    return (
      <WithoutProfile
        identity={portalIdentity(me.error)}
        onCreated={(saved) => {
          if (saved.signup_granted) setWelcome({ saved, open: true });
        }}
      />
    );
  }

  if (me.data !== undefined) {
    return (
      <WelcomeContext.Provider value={welcome?.open ?? false}>
        {children}
        <Notices notices={me.data.notices} />
        <WelcomeDialog
          saved={welcome?.saved ?? null}
          open={welcome?.open ?? false}
          onClose={() => setWelcome((was) => (was === null ? was : { ...was, open: false }))}
        />
      </WelcomeContext.Provider>
    );
  }

  if (me.error !== null) return <NoticeScreen message={me.error.message} retry />;
  return <BootScreen />;
}
