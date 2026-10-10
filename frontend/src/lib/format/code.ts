/**
 * Codes (DESIGN.md §10). A code is what VIS hands out and a user redeems for tokens; a
 * voucher code is the text under a voucher's QR. Both are shown in groups of four. The
 * server reads a code whatever its case and with or without dashes, so what is typed is
 * sent as it stands in the field.
 */

const GROUP = 4;

/** The letters and digits of what was typed, in capitals. Dashes and spaces are separators. */
export function codeCharacters(text: string): string {
  return text.toUpperCase().replace(/[^0-9A-Z]/g, "");
}

/**
 * Groups of four with dashes: `XXXX-XXXX-XXXX`. With `length` (`code_length` or
 * `voucher_code_length` of `/config`) nothing beyond that many characters is kept, which
 * is how a code field stops taking input.
 */
export function formatCode(text: string, length?: number): string {
  const characters = codeCharacters(text);
  const kept = length === undefined ? characters : characters.slice(0, length);
  return kept.match(new RegExp(`.{1,${GROUP}}`, "g"))?.join("-") ?? "";
}

/**
 * What a scanned QR holds of a code (DESIGN.md §13, Wallet). A code card carries a link,
 * `/wallet?code=...`: the `code` is taken out of it. Anything that is no link is taken as
 * it is. A link without a `code` gives null, and the sheet says so. A link itself is
 * never returned, so it can never be sent to the API.
 */
export function readScannedCode(text: string): string | null {
  const scanned = text.trim();
  if (scanned === "") return null;

  let link: URL;
  try {
    link = new URL(scanned);
  } catch {
    return scanned;
  }
  const code = link.searchParams.get("code")?.trim();
  return code === undefined || code === "" ? null : code;
}
