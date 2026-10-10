"use client";

import {
  CircleCheckIcon,
  InfoIcon,
  LoaderCircleIcon,
  TriangleAlertIcon,
  XIcon,
} from "lucide-react";
import { Toaster as Sonner, type ToasterProps } from "sonner";

import { TOAST_MS } from "@/lib/design";

/**
 * Toasts (DESIGN.md §12): the confirmation of a finished action whose result is in view
 * already, at the bottom of the screen above the tab bar, for 4 s, on `--raised` with
 * white text. Each has a close cross at its right end, in a 44 px target. An error is
 * never shown only as a toast: a toast disappears.
 *
 * sonner styles its toasts itself and wins over a plain class, so what the cross needs is
 * said in sonner's own variables and with classes that insist.
 */
const Toaster = (props: ToasterProps) => {
  const aboveTabBar = { bottom: "calc(var(--tab-bar-space) + 0.75rem)" };

  return (
    <Sonner
      theme="dark"
      position="bottom-center"
      duration={TOAST_MS}
      offset={aboveTabBar}
      mobileOffset={aboveTabBar}
      className="toaster group print:hidden"
      closeButton
      icons={{
        success: <CircleCheckIcon className="size-5" />,
        info: <InfoIcon className="size-5" />,
        warning: <TriangleAlertIcon className="size-5" />,
        error: <TriangleAlertIcon className="size-5" />,
        loading: <LoaderCircleIcon className="size-5 animate-spin" />,
        close: <XIcon aria-hidden className="size-5" />,
      }}
      toastOptions={{
        closeButtonAriaLabel: "Close",
        style: { boxShadow: "none", fontFamily: "var(--font-sans)", fontSize: "0.875rem" },
        classNames: {
          // Room at the right end for the cross: its 44 px and 4 px on either side.
          toast: "pr-13!",
          closeButton:
            "top-1/2! size-11! rounded-md! border-0! bg-transparent! text-foreground! hover:bg-card! focus-visible:shadow-none!",
        },
      }}
      style={
        {
          "--normal-bg": "var(--raised)",
          "--normal-text": "var(--foreground)",
          "--normal-border": "var(--raised)",
          "--border-radius": "var(--radius-md)",
          // The cross stands at the right end of the toast, at half its height.
          "--toast-close-button-start": "auto",
          "--toast-close-button-end": "0.25rem",
          "--toast-close-button-transform": "translateY(-50%)",
        } as React.CSSProperties
      }
      {...props}
    />
  );
};

export { Toaster };
