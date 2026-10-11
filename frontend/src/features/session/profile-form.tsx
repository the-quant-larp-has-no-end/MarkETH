"use client";

import { type FormEvent, useEffect, useRef } from "react";

import { SubmitError } from "@/components/feedback";
import { PickerField, revealFirstInvalid, TextField } from "@/components/form";
import { Button } from "@/components/ui/button";
import { PolicyCheckbox } from "@/features/policy";
import { placeError } from "@/lib/api/errors";
import { saveProfileMutation } from "@/lib/api/mutations";
import type { DegreeLevel, MeResponse, ProfileSavedResponse } from "@/lib/api/schema";
import { useConfig } from "@/lib/api/session";
import { useSubmit } from "@/lib/api/submit";
import { useDraft, useRetryAfter } from "@/lib/hooks";

const FIELDS = ["username", "degree_level", "degree_programme", "policy_accepted"] as const;

export type ProfileFormProps = {
  /** The profile to start from. Without it the fields start empty, as on the first visit. */
  initial?: Pick<MeResponse, "username" | "degree_level" | "degree_programme">;
  /**
   * The save creates the profile: the set-up screen. The form then has the checkbox "I
   * accept the usage policy" and sends `policy_accepted: true`. Without it the form
   * changes a profile and sends the three fields alone.
   */
  creating?: boolean;
  /** "Create profile" on the set-up screen, "Save" on the Profile screen. */
  submitLabel: string;
  /** Keep the button disabled until something differs from `initial`. */
  requireChange?: boolean;
  /**
   * The button is as wide as the form at every width: the set-up screen, which is one
   * narrow column like a dialog. Without it the button is full width on phones only.
   */
  wideSubmit?: boolean;
  /** Called with the API's answer once the profile is saved. */
  onSaved?: (saved: ProfileSavedResponse) => void;
};

/**
 * The three fields a profile is made of, and the button that saves them with
 * `PUT /me/profile` (DESIGN.md §13). The set-up screen and the Profile screen both show
 * this form; on the set-up screen (`creating`) it ends with the usage-policy checkbox,
 * which has to be ticked before the profile can be created.
 *
 * The username's lengths and the list of degrees come from `/config`; level and programme
 * are chosen with the picker (DESIGN.md §12), the programme once a level is set. The API decides
 * what is valid: `USERNAME_TAKEN` and a `VALIDATION_ERROR` appear on the field they name,
 * with the API's message, and the other fields keep their input; one that names
 * `policy_accepted` stands under the checkbox. Without an answer, or with a 503, the
 * banner stands above the button and the input stays; saving again is safe.
 *
 * The set-up screen keeps what is entered while it is away (`useDraft`): whoever reads the
 * usage policy and comes back finds the fields and the box as they were left.
 */
export function ProfileForm({
  initial,
  creating = false,
  submitLabel,
  requireChange = false,
  wideSubmit = false,
  onSaved,
}: ProfileFormProps) {
  const config = useConfig();
  // A second submit is stopped where it is made, not by what the last render knew.
  const save = useSubmit(saveProfileMutation);
  const [entered, keep, forget] = useDraft<{
    username: string;
    level: DegreeLevel | "";
    programme: string;
    accepted: boolean;
  }>(creating ? "profile-setup" : null, {
    username: initial?.username ?? "",
    level: initial?.degree_level ?? "",
    programme: initial?.degree_programme ?? "",
    accepted: false,
  });
  const { username, level, programme, accepted } = entered;
  const wait = useRetryAfter(save.error);
  const form = useRef<HTMLFormElement>(null);

  // The button stands below the fields: on a short screen a refused field is above what
  // is shown, so it is brought into view and takes the focus.
  useEffect(() => {
    if (save.error !== null) revealFirstInvalid(form.current);
  }, [save.error]);

  const placed = save.error ? placeError(save.error, FIELDS) : null;
  const programmes = config.degrees.find((degree) => degree.level === level)?.programmes ?? [];
  const filled = username !== "" && level !== "" && programme !== "" && (!creating || accepted);
  const changed =
    username !== initial?.username ||
    level !== initial?.degree_level ||
    programme !== initial?.degree_programme;

  /** An answer is about what was sent: it holds until the input changes. */
  const edited = () => {
    if (save.isError) save.reset();
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!filled) return;
    const profile = { username, degree_level: level, degree_programme: programme };
    // The box is ticked, or the form would not be `filled`: the save says what it shows.
    save.mutate(creating ? { ...profile, policy_accepted: true } : profile, {
      onSuccess: (saved) => {
        forget();
        onSaved?.(saved);
      },
    });
  };

  return (
    <form
      ref={form}
      noValidate
      onSubmit={submit}
      className="flex flex-col gap-4"
      data-slot="profile-form"
    >
      <TextField
        label="Username"
        name="username"
        value={username}
        // What the field shows is what is sent: lower case, and at most as long as allowed.
        onChange={(event) => {
          keep({
            ...entered,
            username: event.target.value.toLowerCase().slice(0, config.username_max_length),
          });
          edited();
        }}
        maxLength={config.username_max_length}
        autoCapitalize="none"
        autoCorrect="off"
        autoComplete="nickname"
        spellCheck={false}
        error={placed?.fields.username}
        help={`${config.username_min_length} to ${config.username_max_length} characters: lower-case letters, digits and underscores.`}
      />
      <PickerField
        label="Level"
        name="degree_level"
        value={level}
        onChange={(next) => {
          // A programme belongs to its level.
          keep({ ...entered, level: next as DegreeLevel, programme: "" });
          edited();
        }}
        options={config.degrees.map((degree) => ({ value: degree.level, label: degree.name }))}
        placeholder="Choose a level"
        searchLabel="Search levels"
        noMatch="No level matches."
        error={placed?.fields.degree_level}
      />
      <PickerField
        label="Programme"
        name="degree_programme"
        value={programme}
        onChange={(next) => {
          keep({ ...entered, programme: next });
          edited();
        }}
        options={programmes.map((label) => ({ value: label, label }))}
        placeholder="Choose a programme"
        searchLabel="Search programmes"
        noMatch="No programme matches."
        disabled={level === ""}
        error={placed?.fields.degree_programme}
      />

      <p className="type-body-sm text-muted-foreground">
        Other players can see your username, your degree, your tokens, your trading record, and what
        you write in live chats.
      </p>

      {creating && (
        <PolicyCheckbox
          checked={accepted}
          onChange={(next) => {
            keep({ ...entered, accepted: next });
            edited();
          }}
          error={placed?.fields.policy_accepted}
        />
      )}

      <SubmitError error={save.error} message={placed?.form} />

      <Button
        type="submit"
        width={wideSubmit ? "full" : "responsive"}
        loading={save.isPending}
        disabled={!filled || (requireChange && !changed) || wait > 0}
      >
        {submitLabel}
      </Button>
    </form>
  );
}
