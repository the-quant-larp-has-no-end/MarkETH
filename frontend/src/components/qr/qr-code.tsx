"use client";

import { QRCodeSVG } from "qrcode.react";

import { CodeText } from "@/components/money";
import { cn } from "@/lib/utils";

/**
 * A QR code (DESIGN.md §12): always `--ink` modules on a `--foreground` tile with 16 px
 * padding and `--radius-lg`, at least 240 px wide. A dark-themed QR does not scan
 * reliably, so the tile is light on every surface. It is an inline SVG: no image is
 * requested and nothing leaves the page.
 *
 * `code` repeats the code as text below the tile, in the code style. For a voucher that
 * is the QR's own content; on a code card the QR holds the link and the text the code.
 */
export function QrCode({
  value,
  label,
  code,
  className,
}: {
  /** What the QR holds. */
  value: string;
  /** What it is, for a screen reader: "Voucher QR code". */
  label: string;
  /** The code to repeat as text below the tile. */
  code?: string;
  className?: string;
}) {
  return (
    <figure data-slot="qr-code" className={cn("flex flex-col items-center gap-3", className)}>
      <div className="w-full max-w-72 min-w-60 rounded-lg bg-foreground p-4 text-ink">
        <QRCodeSVG
          value={value}
          size={256}
          level="M"
          bgColor="transparent"
          fgColor="currentColor"
          role="img"
          aria-label={label}
          className="h-auto w-full"
        />
      </div>
      {code !== undefined && (
        <figcaption>
          <CodeText value={code} className="type-number-lg" />
        </figcaption>
      )}
    </figure>
  );
}
