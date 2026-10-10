"use client";

import { useMutation } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";

import { ErrorBanner, InlineError, RetryCountdown } from "@/components/feedback";
import { SelectField, TextField } from "@/components/form";
import { Button } from "@/components/ui/button";
import { placeError } from "@/lib/api/errors";
import { saveProfileMutation } from "@/lib/api/mutations";
import type { DegreeLevel, MeResponse, ProfileSavedResponse } from "@/lib/api/schema";
import { useConfig } from "@/lib/api/session";
import { useRetryAfter } from "@/lib/hooks";

const FIELDS = ["username", "degree_level", "degree_programme"] as const;

export type ProfileFormProps = {
  /** The profile to start from. Without it the fields start empty, as on the first visit. */
  initial?: Pick<MeResponse, "username" | "degree_level" | "degree_programme">;
  /** "Create profile" on the set-up screen, "Save" on the Profile screen. */
  submitLabel: string;
  /** Keep the button disabled until something differs from `initial`. */
  requireChange?: boolean;
  /** Called with the API's answer once the profile is saved. */
  onSaved?: (saved: ProfileSavedResponse) => void;
};

/**
 * The three fields a profile is made of, and the button that saves them with
 * `PUT /me/profile` (DESIGN.md §13). The set-up screen and the Profile screen both show
 * exactly this form.
 *
 * The username's lengths and the list of degrees come from `/config`. The API decides
 * what is valid: `USERNAME_TAKEN` and a `VALIDATION_ERROR` appear on the field they name,
 * with the API's message, and the other fields keep their input. Without an answer, or
 * with a 503, the banner stands above the button and the input stays; saving again is
 * safe.
 */
export function ProfileForm({
  initial,
  submitLabel,
  requireChange = false,
  onSaved,
}: ProfileFormProps) {
  const config = useConfig();
  const save = useMutation(saveProfileMutation);
  const [username, setUsername] = useState(initial?.username ?? "");
  const [level, setLevel] = useState<DegreeLevel | "">(initial?.degree_level ?? "");
  const [programme, setProgramme] = useState(initial?.degree_programme ?? "");
  const wait = useRetryAfter(save.error);

  const placed = save.error ? placeError(save.error, FIELDS) : null;
  const programmes = config.degrees.find((degree) => degree.level === level)?.programmes ?? [];
  const filled = username !== "" && level !== "" && programme !== "";
  const changed =
    username !== initial?.username ||
    level !== initial?.degree_level ||
    programme !== initial?.degree_programme;

  /** An answer is about what was sent: once the input changes, it no longer applies. */
  const edited = () => {
    if (save.isError) save.reset();
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!filled || save.isPending) return;
    save.mutate(
      { username, degree_level: level, degree_programme: programme },
      { onSuccess: onSaved },
    );
  };

  return (
    <form noValidate onSubmit={submit} className="flex flex-col gap-4" data-slot="profile-form">
      <TextField
        label="Username"
        name="username"
        value={username}
        // What the field shows is what is sent: lower case, and no longer than allowed.
        onChange={(event) => {
          setUsername(event.target.value.toLowerCase().slice(0, config.username_max_length));
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
      <SelectField
        label="Level"
        name="degree_level"
        value={level}
        onChange={(next) => {
          setLevel(next as DegreeLevel);
          // A programme belongs to its level.
          setProgramme("");
          edited();
        }}
        options={config.degrees.map((degree) => ({ value: degree.level, label: degree.name }))}
        placeholder="Choose a level"
        error={placed?.fields.degree_level}
      />
      <SelectField
        label="Programme"
        name="degree_programme"
        value={programme}
        onChange={(next) => {
          setProgramme(next);
          edited();
        }}
        options={programmes.map((label) => ({ value: label, label }))}
        placeholder="Choose a programme"
        disabled={level === ""}
        error={placed?.fields.degree_programme}
      />

      <p className="type-body-sm text-muted-foreground">
        Other players can see your username, your degree, your tokens and your trading record.
      </p>

      {save.error && placed?.form != null && (
        <div className="flex flex-col gap-1">
          {save.error.outcomeUnknown ? (
            <ErrorBanner error={save.error} />
          ) : (
            <InlineError>{placed.form}</InlineError>
          )}
          <RetryCountdown error={save.error} />
        </div>
      )}

      <Button
        type="submit"
        loading={save.isPending}
        disabled={!filled || (requireChange && !changed) || wait > 0}
      >
        {submitLabel}
      </Button>
    </form>
  );
}
