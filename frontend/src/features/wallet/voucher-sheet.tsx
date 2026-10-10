"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { InlineError, QueryState, RetryCountdown } from "@/components/feedback";
import { ProductRow, RowList, RowSkeletons } from "@/components/rows";
import { ResponsiveSheet, SheetBody, SheetFooter } from "@/components/sheet";
import { Button } from "@/components/ui/button";
import { messageOf } from "@/lib/api/errors";
import { productsQuery } from "@/lib/api/queries";
import type { VoucherIssuedResponse } from "@/lib/api/schema";
import { useConfig } from "@/lib/api/session";
import { formatCount, formatTokens } from "@/lib/format";
import { useRetryAfter } from "@/lib/hooks";

import { VoucherView } from "./voucher-view";
import type { Write } from "./write";

const TITLE = "Get a voucher";

/**
 * "Get a voucher" (DESIGN.md §13): a sheet with two steps, then the voucher.
 *
 * 1. The products of `GET /vouchers/products`, in the API's order. A product the caller
 *    cannot have now is disabled and stays in the list: `affordable` is the server's
 *    answer, and nothing here looks at the balance or at what a product takes.
 * 2. One sentence that says what happens, "Get voucher" and "Back". "Get voucher" sends
 *    the product's `id` to `POST /vouchers`.
 * 3. The answer, shown as the voucher. Its code is in that answer and nowhere else.
 *
 * A refusal is shown above the button with the API's message. A request that got no
 * answer is not this sheet's to show: the Wallet closes the sheet and says so itself.
 *
 * Give it another `key` for every opening, so that each starts at the list. Its state stays
 * while it closes: a sheet that slides away still shows the step it was on.
 */
export function VoucherSheet({
  open,
  onClose,
  issue,
}: {
  open: boolean;
  onClose: () => void;
  /** `POST /vouchers`, held by the Wallet so that it outlives the sheet. */
  issue: Write<VoucherIssuedResponse, string>;
}) {
  const config = useConfig();
  const products = useQuery({ ...productsQuery, enabled: open });
  const [productId, setProductId] = useState<string | null>(null);
  const [issued, setIssued] = useState(false);
  const wait = useRetryAfter(issue.error);

  // The product as the list has it now: after a refusal the list is read again, and what
  // it says of the product then is what counts.
  const product = products.data?.items.find((item) => item.id === productId);
  const refusal = issue.error !== null && !issue.error.outcomeUnknown ? issue.error : null;
  const days = config.voucher_expiry_days;

  /**
   * A refusal is about the product that was asked for: with another choice it no longer
   * applies. The wait after `RATE_LIMITED` is the exception: it belongs to the caller.
   */
  const dropRefusal = () => {
    if (wait === 0) issue.forget();
  };

  return (
    <ResponsiveSheet
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      title={issued ? (issue.data?.product_name ?? product?.name ?? TITLE) : TITLE}
      dismissible={!issue.sending}
    >
      {issued ? (
        <VoucherView voucher={issue.data} onDone={onClose} />
      ) : product !== undefined ? (
        <>
          <SheetBody>
            <p className="type-body">
              Turn {formatTokens(product.price_cr)} into a voucher for {product.name}?
            </p>
            <p className="type-body-sm text-muted-foreground">
              Valid for {formatCount(days)} {days === 1 ? "day" : "days"}. An unused voucher is
              refunded.
            </p>
          </SheetBody>
          <SheetFooter>
            {refusal !== null && (
              <div className="flex flex-col gap-1">
                <InlineError>{messageOf(refusal)}</InlineError>
                <RetryCountdown error={refusal} />
              </div>
            )}
            <Button
              width="full"
              loading={issue.sending}
              disabled={!product.affordable || issue.blocked || wait > 0}
              onClick={() => issue.send(product.id, { onSuccess: () => setIssued(true) })}
            >
              Get voucher
            </Button>
            <Button
              variant="ghost"
              width="full"
              disabled={issue.sending}
              onClick={() => {
                dropRefusal();
                setProductId(null);
              }}
            >
              Back
            </Button>
          </SheetFooter>
        </>
      ) : (
        // No pinned action below the list: the list itself keeps clear of the safe area.
        <SheetBody className="pb-[max(1rem,env(safe-area-inset-bottom))]">
          <QueryState query={products} skeleton={<RowSkeletons count={5} />}>
            {(list) =>
              list.items.length === 0 ? (
                <p className="type-body text-muted-foreground">
                  Nothing to get a voucher for right now.
                </p>
              ) : (
                <RowList>
                  {list.items.map((item) => (
                    <ProductRow
                      key={item.id}
                      product={item}
                      onSelect={(picked) => {
                        dropRefusal();
                        setProductId(picked.id);
                      }}
                    />
                  ))}
                </RowList>
              )
            }
          </QueryState>
        </SheetBody>
      )}
    </ResponsiveSheet>
  );
}
