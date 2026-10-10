"use client";

import { toast } from "sonner";

import { Screen, TextLink } from "@/components/shell";
import { DateTime } from "@/components/time";
import { Card } from "@/components/ui/card";
import { ProfileForm } from "@/features/session";
import type { MeResponse } from "@/lib/api/schema";
import { useMe } from "@/lib/api/session";

/**
 * Profile (`/profile`): DESIGN.md §13. An inner screen, reached from the profile button
 * of the top bar. It changes the two things the set-up asks for, the username and the
 * degree, with the same form (`ProfileForm` of `@/features/session`), so every state of
 * that form is a state of this screen: `USERNAME_TAKEN` and a `VALIDATION_ERROR` on the
 * field they name, the banner above the button when no answer came.
 *
 * It reads nothing itself: the profile is the `/me` the app already holds, and the answer
 * of a save takes its place. There is no welcome dialog here and nothing to sign out of.
 */
export function ProfileScreen() {
  const me = useMe();

  return (
    <Screen top="inner" title="Profile" width="narrow">
      <ProfileCard me={me} />
      <ProfileForm
        initial={me}
        submitLabel="Save"
        requireChange
        // Success stays on the screen and says so.
        onSaved={() => toast("Profile saved")}
      />
    </Screen>
  );
}

/**
 * Who the viewer is: the username and the degree other players see, and under a divider
 * the name and the address the portal sent. This card and the admin area are the only
 * places a real name or an address is shown (DESIGN.md §10, People).
 *
 * Under a second divider: when the usage policy was accepted, which is `policy_accepted_at`
 * of `/me` with its time of day, and the link to the policy. It was accepted when the
 * profile was created, so there is nothing to tick here.
 */
function ProfileCard({ me }: { me: MeResponse }) {
  return (
    <Card data-slot="profile-card">
      <dl className="flex flex-col gap-1">
        <dt className="sr-only">Username</dt>
        <dd className="type-h2 wrap-break-word">{me.username}</dd>
        <dt className="sr-only">Degree</dt>
        <dd className="type-body-sm text-muted-foreground">{me.degree_programme}</dd>
      </dl>
      <div className="flex flex-col gap-2 border-t pt-3">
        <dl className="flex flex-col gap-1">
          {/* The portal may send no name; then there is none to show. */}
          {me.name !== "" && (
            <>
              <dt className="sr-only">Name</dt>
              <dd className="type-body wrap-break-word">{me.name}</dd>
            </>
          )}
          <dt className="sr-only">Email</dt>
          <dd className="type-body wrap-break-word">{me.email}</dd>
        </dl>
        <p className="type-caption text-muted-foreground">
          From your ETH sign-in. Only you and VIS see these.
        </p>
      </div>
      <div
        data-slot="profile-policy"
        className="flex flex-col border-t pt-3 type-body-sm text-muted-foreground"
      >
        <p>
          You accepted the usage policy on <DateTime value={me.policy_accepted_at} />.
        </p>
        {/* A line of its own, as wide as its words. Its 44 px target reaches into the
            card's padding, so the words end 16 px above the card's edge like the rest. */}
        <TextLink href="/policy" className="-mb-3 self-start">
          Read the usage policy
        </TextLink>
      </div>
    </Card>
  );
}
