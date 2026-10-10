"use client";

import { CircleCheckIcon, InfoIcon, LoaderCircleIcon, TriangleAlertIcon } from "lucide-react";
import { Toaster as Sonner, type ToasterProps } from "sonner";

import { TOAST_MS } from "@/lib/design";

/**
 * Toasts (DESIGN.md §12): the confirmation of a finished action, at the bottom of the
 * screen above the tab bar, for 4 s, on `--raised` with white text. An error is never
 * shown only as a toast: a toast disappears.
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
      icons={{
        success: <CircleCheckIcon className="size-5" />,
        info: <InfoIcon className="size-5" />,
        warning: <TriangleAlertIcon className="size-5" />,
        error: <TriangleAlertIcon className="size-5" />,
        loading: <LoaderCircleIcon className="size-5 animate-spin" />,
      }}
      toastOptions={{
        style: { boxShadow: "none", fontFamily: "var(--font-sans)", fontSize: "0.875rem" },
      }}
      style={
        {
          "--normal-bg": "var(--raised)",
          "--normal-text": "var(--foreground)",
          "--normal-border": "var(--raised)",
          "--border-radius": "var(--radius-md)",
        } as React.CSSProperties
      }
      {...props}
    />
  );
};

export { Toaster };
