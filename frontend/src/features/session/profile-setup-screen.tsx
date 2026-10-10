"use client";

import { Illustration, Logo } from "@/components/brand";
import type { ProfileSavedResponse } from "@/lib/api/schema";

import { ProfileForm } from "./profile-form";

/**
 * Profile set-up (DESIGN.md §13): shown in place of the route while `/me` answers
 * `PROFILE_REQUIRED`, which is the first visit. No top bar, no tab bar, and the address
 * stays as it was opened, so someone who scanned a code card arrives in the Wallet with
 * the code once the profile is saved. This is the one thing that may stand in front of
 * the Markets list: a logo, three fields and one button.
 */
export function ProfileSetupScreen({
  identity,
  onCreated,
}: {
  /** Who the portal says is calling (`details` of `PROFILE_REQUIRED`). */
  identity: { name: string; email: string } | null;
  onCreated: (saved: ProfileSavedResponse) => void;
}) {
  // The name the portal sent; when that is empty, the address instead.
  const signedInAs = identity === null ? "" : identity.name || identity.email;

  return (
    <main
      data-slot="profile-setup"
      className="mx-auto flex min-h-dvh w-full max-w-[26.25rem] flex-col justify-center gap-6 px-4 py-8"
    >
      <h1 className="sr-only">Set up your profile</h1>
      <div className="flex flex-col items-center gap-4 text-center">
        <Logo size="large" />
        <p className="type-body">
          Trade on campus questions with free tokens. Enough of them get you a coffee at the bar.
        </p>
      </div>

      {signedInAs !== "" && (
        <p className="type-body-sm text-muted-foreground">Signed in as {signedInAs}</p>
      )}

      <ProfileForm submitLabel="Create profile" wideSubmit onSaved={onCreated} />

      <footer className="flex flex-col items-center gap-2 pt-2 text-center">
        <Illustration name="dome" />
        <p className="type-body-sm text-muted-foreground">
          Pick a name for the leaderboard. The podium remembers.
        </p>
      </footer>
    </main>
  );
}
