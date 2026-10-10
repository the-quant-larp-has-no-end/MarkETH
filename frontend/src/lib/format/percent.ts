import { EN_DASH, TIMES } from "./amount";

/**
 * Probabilities, rates, ratios and counts arrive as JSON numbers the server has already
 * rounded for display (DESIGN.md §10). They are formatted here and nothing is derived
 * from them: `p_no` comes from the API, the frontend does not subtract.
 */

const LOCALE = "en-GB";

const formatters = new Map<string, Intl.NumberFormat>();

function formatter(key: string, options: Intl.NumberFormatOptions): Intl.NumberFormat {
  let found = formatters.get(key);
  if (found === undefined) {
    found = new Intl.NumberFormat(LOCALE, options);
    formatters.set(key, found);
  }
  return found;
}

/**
 * A probability or a rate as a percentage: `0.548` with one decimal is `54.8%`.
 * `decimals` is `price_display_decimals` of `/config`.
 */
export function formatPercent(fraction: number, decimals: number): string {
  return formatter(`percent:${decimals}`, {
    style: "percent",
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(fraction);
}

/** The same figure for a screen reader: "54.8 percent". */
export function spokenPercent(fraction: number, decimals: number): string {
  return formatPercent(fraction, decimals).replace("%", " percent");
}

/** A rate that may be missing (`win_rate`): a percentage, or an en dash for null. */
export function formatRate(rate: number | null, decimals: number): string {
  return rate === null ? EN_DASH : formatPercent(rate, decimals);
}

/**
 * An average price: tokens per share, shown like a probability without the percent
 * style and with two decimals more. With one display decimal `0.524` is `0.524`.
 */
export function formatAveragePrice(price: number, decimals: number): string {
  const places = decimals + 2;
  return formatter(`price:${places}`, {
    minimumFractionDigits: places,
    maximumFractionDigits: places,
  }).format(price);
}

/** A payout ratio, already cut to two decimals by the server: `1.35×`, or an en dash for null. */
export function formatRatio(ratio: number | null): string {
  if (ratio === null) return EN_DASH;
  const figure = formatter("ratio", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(ratio);
  return figure + TIMES;
}

/** A count (`trades`, `rank`, a number of codes): digits grouped in threes, `1,204`. */
export function formatCount(count: number): string {
  return formatter("count", { maximumFractionDigits: 0 }).format(count);
}

const DECIMAL = /^(\d+)(?:\.(\d+))?$/;

function trimFraction(whole: string, fraction: string): string {
  const cleanWhole = whole.replace(/^0+(?=\d)/, "");
  const cleanFraction = fraction.replace(/0+$/, "");
  return cleanFraction === "" ? cleanWhole : `${cleanWhole}.${cleanFraction}`;
}

/**
 * A typed percentage as the fraction the API takes, by moving the decimal point two
 * places in the string: `62.5` is `0.625`, `100` is `1`. Null for what is no number.
 * Nothing is calculated, so what is sent is exactly what was typed.
 */
export function percentToFraction(text: string): string | null {
  const match = DECIMAL.exec(text.trim());
  if (match === null) return null;
  const whole = match[1]!.padStart(3, "0");
  const fraction = match[2] ?? "";
  return trimFraction(whole.slice(0, -2), whole.slice(-2) + fraction);
}

/** The other way round, for showing a rate filter that is set: `0.625` is `62.5`. */
export function fractionToPercent(text: string): string | null {
  const match = DECIMAL.exec(text.trim());
  if (match === null) return null;
  const fraction = (match[2] ?? "").padEnd(2, "0");
  return trimFraction(match[1]! + fraction.slice(0, 2), fraction.slice(2));
}
