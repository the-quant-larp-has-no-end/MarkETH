"use client";

import "./globals.css";

import { GENERIC_MESSAGE } from "@/lib/api/errors";
import { page } from "@/lib/browser";

/**
 * The last resort: the root layout itself failed, so nothing of the app is on screen and
 * this brings its own document. One sentence and a way to start again, in the tokens.
 */
export default function GlobalError() {
  return (
    <html lang="en">
      <body>
        <main className="mx-auto flex min-h-dvh max-w-[26.25rem] flex-col items-center justify-center gap-6 px-4 text-center">
          <title>MarkETH</title>
          <h1 className="sr-only">MarkETH</h1>
          <p role="alert" className="type-body">
            {GENERIC_MESSAGE}
          </p>
          <button
            type="button"
            onClick={() => page.reload()}
            className="inline-flex h-12 items-center justify-center rounded-md border border-border-strong px-4 type-label text-foreground"
          >
            Try again
          </button>
        </main>
      </body>
    </html>
  );
}
