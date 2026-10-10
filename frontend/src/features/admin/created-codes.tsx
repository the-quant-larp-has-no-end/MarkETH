"use client";

import { DownloadIcon, PrinterIcon, TriangleAlertIcon } from "lucide-react";
import { type RefObject, useEffect, useId, useRef } from "react";

import { Banner } from "@/components/feedback";
import { CodeText } from "@/components/money";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import type { CodeBatchCreatedResponse } from "@/lib/api/schema";
import { formatCount } from "@/lib/format";

import { browser } from "./browser";
import { codesCsv, csvFileName } from "./codes-csv";

/**
 * The codes of a batch that was just created, shown once (DESIGN.md §13). The API sends
 * the plain codes in the answer that created the batch and never again, so they live in
 * memory while this is on screen and nowhere else.
 *
 * Under the banner: "Download CSV" and "Print QR sheet", both made in the browser from
 * that answer, and the codes themselves. "Done" takes them off the screen; the screen
 * asks before it does. A file that was made for a download is let go of together with
 * the codes: nothing of them stays behind in the page.
 */
export function CreatedCodes({
  created,
  headingRef,
  printing = false,
  onPrint,
  onDone,
}: {
  created: CodeBatchCreatedResponse;
  /** The heading of the codes, which takes the focus when they appear. */
  headingRef?: RefObject<HTMLHeadingElement | null>;
  /** The QR sheet is being laid out for the printer. */
  printing?: boolean;
  /** The user asked for the QR sheet on paper. */
  onPrint: () => void;
  /** The user is finished with the codes; `button` is what they tapped to say so. */
  onDone: (button: HTMLButtonElement) => void;
}) {
  const { batch, codes } = created;
  const headingId = useId();
  /** What lets go of each file that was offered for saving. */
  const files = useRef<(() => void)[]>([]);

  useEffect(() => {
    const offered = files.current;
    return () => {
      for (const release of offered.splice(0)) release();
    };
  }, []);

  return (
    <Card data-slot="created-codes" role="region" aria-labelledby={headingId}>
      <Banner icon={TriangleAlertIcon}>These codes cannot be shown again. Save them now.</Banner>
      <div className="flex flex-col gap-1">
        <h2 id={headingId} ref={headingRef} tabIndex={-1} className="type-h3 wrap-break-word">
          {batch.label}
        </h2>
        <p className="type-body-sm text-muted-foreground">
          {codes.length === 1 ? "1 code" : `${formatCount(codes.length)} codes`}
        </p>
      </div>
      <div className="grid grid-cols-1 gap-2 md:flex">
        <Button
          variant="secondary"
          onClick={() =>
            files.current.push(
              browser.saveFile(
                csvFileName(batch.label),
                codesCsv(created),
                "text/csv;charset=utf-8",
              ),
            )
          }
        >
          <DownloadIcon aria-hidden />
          Download CSV
        </Button>
        <Button variant="secondary" loading={printing} onClick={onPrint}>
          <PrinterIcon aria-hidden />
          Print QR sheet
        </Button>
      </div>
      <ul
        aria-label="Codes"
        // The list scrolls in its own box, which the keyboard has to reach to scroll it.
        tabIndex={0}
        className="grid max-h-64 grid-cols-1 gap-x-6 overflow-y-auto rounded-md bg-background px-3 py-2 md:grid-cols-2 lg:grid-cols-3"
      >
        {codes.map(({ code }) => (
          <li key={code} className="py-1 type-body">
            <CodeText value={code} />
          </li>
        ))}
      </ul>
      <Button
        variant="secondary"
        width="fit"
        className="self-start"
        onClick={(event) => onDone(event.currentTarget)}
      >
        Done
      </Button>
    </Card>
  );
}
