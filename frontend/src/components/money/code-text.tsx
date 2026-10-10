import { formatCode } from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * A code or a voucher code as text (DESIGN.md §10): groups of four, Barlow, spaced out.
 * `XXXX-XXXX-XXXX` for a code, `XXXX-XXXX` for a voucher code.
 */
export function CodeText({ value, className }: { value: string; className?: string }) {
  return (
    <span data-slot="code" className={cn("type-code whitespace-nowrap", className)}>
      {formatCode(value)}
    </span>
  );
}
