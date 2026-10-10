import { TokenIcon, type TokenIconSize } from "@/components/brand";
import { formatAmount, formatTokens, isZero, MINUS, splitSign } from "@/lib/format";
import { cn } from "@/lib/utils";

const SIZES = {
  /** A figure in secondary text: `body-sm`, with the 16 px icon. */
  sm: { text: "type-body-sm font-display tabular-nums", icon: 16, gap: "gap-1" },
  /** `number`: an amount in a row or a table. The default. */
  md: { text: "type-number", icon: 16, gap: "gap-1" },
  /** `number-lg`: a balance, a quote figure. */
  lg: { text: "type-number-lg", icon: 20, gap: "gap-1.25" },
  /** `display`: the balance of the Wallet. */
  display: { text: "type-display", icon: 32, gap: "gap-2" },
} as const satisfies Record<string, { text: string; icon: TokenIconSize; gap: string }>;

export type AmountSize = keyof typeof SIZES;

export type AmountProps = {
  /** A `*_cr` field of the API, as the string it arrived as. */
  value: string;
  size?: AmountSize;
  /**
   * Show the whole string. Only on a detail view: the trade receipt and the expanded
   * ledger row. Everywhere else the figure is cut after the second decimal.
   */
  full?: boolean;
  className?: string;
};

/**
 * A standalone amount (DESIGN.md §10): the token icon, then the figure, and no word. The
 * gap between them is a quarter of the icon. A screen reader hears the number and the
 * word "tokens". A figure the API sends with a minus keeps it, before the icon.
 *
 * The value is a string from the response to the screen: this component formats it and
 * never calculates with it. For an amount inside a sentence use `formatTokens`.
 */
export function Amount({ value, size = "md", full = false, className }: AmountProps) {
  const { text, icon, gap } = SIZES[size];
  const { negative, magnitude } = splitSign(value);

  return (
    <span
      data-slot="amount"
      className={cn("inline-flex items-center whitespace-nowrap", text, gap, className)}
    >
      {negative && <span aria-hidden>{MINUS}</span>}
      <TokenIcon size={icon} />
      <span aria-hidden>{formatAmount(magnitude, { full })}</span>
      <span className="sr-only">{formatTokens(value, { full })}</span>
    </span>
  );
}

/**
 * A ledger or payout amount with its sign (DESIGN.md §10): `+` (bean) `200` in
 * `--yes-text` for tokens in, `−` (bean) `25` in `--foreground` for tokens out. Tokens
 * out are not red: a trade or a voucher lowering the balance is normal.
 */
export function SignedAmount({ value, size = "md", full = false, className }: AmountProps) {
  const { text, icon, gap } = SIZES[size];
  const { negative, magnitude } = splitSign(value);
  const sign = negative ? MINUS : isZero(magnitude) ? "" : "+";

  return (
    <span
      data-slot="amount"
      className={cn(
        "inline-flex items-center whitespace-nowrap",
        text,
        gap,
        sign === "+" ? "text-yes-text" : "text-foreground",
        className,
      )}
    >
      {sign !== "" && <span aria-hidden>{sign}</span>}
      <TokenIcon size={icon} />
      <span aria-hidden>{formatAmount(magnitude, { full })}</span>
      <span className="sr-only">
        {sign === "+" ? "+" : ""}
        {formatTokens(value, { full })}
      </span>
    </span>
  );
}
