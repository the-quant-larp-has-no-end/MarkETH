import { screen, waitFor, within } from "@testing-library/react";
import type { UserEvent } from "@testing-library/user-event";
import { expect } from "vitest";

/**
 * Chooses an option of a picker (DESIGN.md §12) as a phone does: a tap on the field named
 * `picker` brings its sheet, a tap on `option` chooses it, and the sheet slides away.
 */
export async function choosePicker(user: UserEvent, picker: string, option: string) {
  await user.click(screen.getByLabelText(picker));
  const sheet = await screen.findByRole("dialog", { name: picker });
  await user.click(within(sheet).getByRole("option", { name: option }));
  // The bottom sheet takes a moment to slide away before it is gone.
  await waitFor(
    () => expect(screen.queryByRole("dialog", { name: picker })).not.toBeInTheDocument(),
    { timeout: 3000 },
  );
}
