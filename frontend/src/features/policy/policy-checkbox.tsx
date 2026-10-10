"use client";

import { CheckboxField } from "@/components/form";
import { TextLink } from "@/components/shell";

/**
 * "I accept the usage policy" (DESIGN.md §13): the checkbox of the profile set-up and of
 * the propose form. The words "usage policy" are a link to `/policy`, in the same tab:
 * both forms keep what was typed (`useDraft`), and show it again to whoever comes back.
 * The link is a target of its own: a tap on it leaves the box as it is.
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
  return (
    <CheckboxField
      name="policy_accepted"
      checked={checked}
      onChange={onChange}
      error={error}
      disabled={disabled}
    >
      I accept the <TextLink href="/policy">usage policy</TextLink>
    </CheckboxField>
  );
}
