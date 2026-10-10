/**
 * Brings the first field the API refused into view and gives it the focus. A long form is
 * sent from its last line, and an error that stands on a field further up would
 * otherwise be an error nobody sees. A screen reader lands on the field and hears the
 * API's message, which the field names as its description.
 *
 * A group of choices cannot take the focus; it is scrolled into view all the same.
 */
export function revealFirstInvalid(container: HTMLElement | null): void {
  const invalid = container?.querySelector<HTMLElement>('[aria-invalid="true"]');
  if (invalid === null || invalid === undefined) return;
  invalid.scrollIntoView({ block: "center" });
  invalid.focus({ preventScroll: true });
}
