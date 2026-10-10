"use client";

import { Amount } from "@/components/money";
import type { VoucherProductItem } from "@/lib/api/schema";
import { cn } from "@/lib/utils";

import { rowClasses, RowFigure, RowItem, rowPressClasses, RowText } from "./list-row";

/**
 * One product a voucher can be for (DESIGN.md §12): the name as the API sends it, then
 * the token icon and what it takes. The whole row is disabled when `affordable` is
 * false, and stays in the list. `affordable` is the server's answer; the frontend looks
 * at neither the balance nor the amount. This is a list, not a shop: no image, no
 * quantity, nothing that names a price.
 */
export function ProductRow({
  product,
  onSelect,
}: {
  product: VoucherProductItem;
  onSelect: (product: VoucherProductItem) => void;
}) {
  return (
    <RowItem>
      <button
        type="button"
        disabled={!product.affordable}
        onClick={() => onSelect(product)}
        className={cn(
          rowClasses,
          rowPressClasses,
          "disabled:pointer-events-none disabled:text-subtle",
        )}
      >
        <RowText title={product.name} />
        <RowFigure>
          <Amount value={product.price_cr} />
        </RowFigure>
      </button>
    </RowItem>
  );
}
