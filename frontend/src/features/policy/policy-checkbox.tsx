"use client";

import { useId } from "react";

import { CheckboxField } from "@/components/form";
import { TextLink } from "@/components/shell";

/**
 * "I accept the usage policy" (DESIGN.md §13): the checkbox of the profile set-up and of
 * the propose form. The words "usage policy" are a link to `/policy`, which opens in a
 * tab of its own, so the form keeps what was typed. The link is a target of its own: a
 * tap on it leaves the box as it is.
 *
 * The request that follows carries `policy_accepted: true`. A `VALIDATION_ERROR` that
 * names `policy_accepted` is passed in as `error` and stands under the row, with the API's
 * sentence.
 */
export function PolicyCheckbox({
  checked,
  onChange,
  error,
  disabled = false,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  /** The API's message for `policy_accepted` (`placeError`). */
  error?: string | null;
  disabled?: boolean;
}) {
  const noteId = useId();

  return (
    <>
      <CheckboxField
        name="policy_accepted"
        checked={checked}
        onChange={onChange}
        error={error}
        disabled={disabled}
      >
        I accept the{" "}
        <TextLink href="/policy" newTab aria-describedby={noteId}>
          usage policy
        </TextLink>
      </CheckboxField>
      {/* Outside the row, so that it is no part of the checkbox's name. */}
      <span id={noteId} hidden>
        Opens in another tab.
      </span>
    </>
  );
}
