"use client";

import { Logo } from "@/components/brand";
import { Button } from "@/components/ui/button";
import { page } from "@/lib/browser";

/**
 * The full-screen message (DESIGN.md §13): plain on purpose. No top bar, no tab bar, the
 * logo with name centred, and under it one message. The message is the API's, as sent;
 * only the portal's 401 and a missing answer have a text of ours, because nothing was
 * sent. There is no illustration and no joke.
 *
 * "Try again" reloads the page. It is offered where reloading can help, and not to
 * somebody the API has told it is not open to them.
 */
export function NoticeScreen({ message, retry = false }: { message: string; retry?: boolean }) {
  return (
    <main
      data-slot="notice-screen"
      className="mx-auto flex min-h-dvh w-full max-w-[26.25rem] flex-col items-center justify-center gap-6 px-4 py-8 text-center"
    >
      <h1 className="sr-only">MarkETH</h1>
      <Logo size="large" />
      <p role="alert" className="type-body">
        {message}
      </p>
      {retry && (
        <Button variant="secondary" width="fit" onClick={() => page.reload()}>
          Try again
        </Button>
      )}
    </main>
  );
}
