import { type ReactNode, useId } from "react";

import { Card, CardHeader } from "@/components/ui/card";

/**
 * One section of Market detail under its heading: "Your position", "Rules", "If the
 * market is voided", "Resolution". A card, as everything on `--background` that belongs
 * together; the heading is an `h2` in the `h3` look, because the screen's `h1` is the
 * question (DESIGN.md §15: the styles are looks, not levels).
 */
export function DetailSection({
  title,
  aside,
  slot,
  children,
}: {
  title: string;
  /** Something that stands beside the heading: the outcome badge of a resolution. */
  aside?: ReactNode;
  /** The `data-slot` of the section. */
  slot: string;
  children: ReactNode;
}) {
  const titleId = useId();

  return (
    <section aria-labelledby={titleId} data-slot={slot}>
      <Card>
        <CardHeader className="items-center">
          <h2 id={titleId} className="type-h3">
            {title}
          </h2>
          {aside}
        </CardHeader>
        {children}
      </Card>
    </section>
  );
}

/** A label and what the API sent for it: one entry of a section's `dl`. */
export function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <dt className="type-caption text-muted-foreground">{label}</dt>
      <dd className="type-body wrap-break-word whitespace-pre-line">{children}</dd>
    </div>
  );
}
