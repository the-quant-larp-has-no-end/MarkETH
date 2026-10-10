/**
 * Amounts (`*_cr`) and share quantities are decimal strings, and stay strings from the
 * response to the screen and back into a request (DESIGN.md §10). These helpers are the
 * only code that looks inside one, and they work on its characters: nothing here turns
 * an amount into a number, rounds it, compares it or adds to it.
 */

/** U+2212. The sign of a negative amount on screen. */
export const MINUS = "−";
/** U+2013. Stands for a figure the API has none of. */
export const EN_DASH = "–";
/** U+00D7. Follows a payout ratio. */
export const TIMES = "×";

/**
 * The one thing read from an amount. The API trims zeros, so an amount of zero always
 * arrives as the single character `0`. A screen may use this to leave out what would be
 * empty (a holding of no shares, a payout of nothing). It is a test of the string: two
 * amounts are never ordered, added or measured against a limit here; `allowed`,
 * `affordable`, `can_trade` and `can_get_voucher` are the server's answers to that.
 */
export function isZero(amount: string): boolean {
  return amount === "0";
}

/** Takes the sign off: a ledger line and a few risk figures arrive with a minus. */
export function splitSign(amount: string): { negative: boolean; magnitude: string } {
  return amount.startsWith("-")
    ? { negative: true, magnitude: amount.slice(1) }
    : { negative: false, magnitude: amount };
}

/** Groups the whole part in threes by inserting commas into the string: `1250.5` is `1,250.5`. */
export function groupDigits(magnitude: string): string {
  const point = magnitude.indexOf(".");
  const whole = point === -1 ? magnitude : magnitude.slice(0, point);
  const decimals = point === -1 ? "" : magnitude.slice(point);
  return whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",") + decimals;
}

/**
 * Cuts the string after `places` decimals. Cutting, not rounding, so the app never shows
 * more than there is: `1.999999` is `1.99`, never `2`.
 */
export function cutDecimals(magnitude: string, places = 2): string {
  const point = magnitude.indexOf(".");
  if (point === -1) return magnitude;
  return places <= 0 ? magnitude.slice(0, point) : magnitude.slice(0, point + 1 + places);
}

export type AmountFormat = {
  /**
   * The whole string, as on the trade receipt and the expanded ledger row. Without it
   * the figure is cut after the second decimal, which is right everywhere else.
   */
  full?: boolean;
};

/** An amount or a share quantity as its figure: grouped, cut unless `full`, with U+2212 for a minus. */
export function formatAmount(amount: string, { full = false }: AmountFormat = {}): string {
  const { negative, magnitude } = splitSign(amount);
  const figure = groupDigits(full ? magnitude : cutDecimals(magnitude));
  return negative ? MINUS + figure : figure;
}

/** The unit's name after a figure in a sentence: "token" when the string is `1`, otherwise "tokens". */
export function tokenWord(amount: string): "token" | "tokens" {
  return amount === "1" ? "token" : "tokens";
}

/** An amount inside a sentence, with the word and no icon: "240 tokens". */
export function formatTokens(amount: string, format: AmountFormat = {}): string {
  return `${formatAmount(amount, format)} ${tokenWord(amount)}`;
}

/** "share" when the string is `1`, otherwise "shares". */
export function shareWord(shares: string): "share" | "shares" {
  return shares === "1" ? "share" : "shares";
}

/** A share quantity with its word, and its outcome when one is given: "183.29 YES shares". */
export function formatShares(
  shares: string,
  { outcome, ...format }: AmountFormat & { outcome?: string } = {},
): string {
  const figure = formatAmount(shares, format);
  return outcome === undefined
    ? `${figure} ${shareWord(shares)}`
    : `${figure} ${outcome} ${shareWord(shares)}`;
}
