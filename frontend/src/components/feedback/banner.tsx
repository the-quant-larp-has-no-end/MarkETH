"use client";

import { InfoIcon, type LucideIcon, TriangleAlertIcon } from "lucide-react";
import type { ReactNode } from "react";

import { Illustration, type IllustrationName } from "@/components/brand";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { type ApiError, messageOf } from "@/lib/api/errors";
import { useRetryAfter } from "@/lib/hooks";
import { cn } from "@/lib/utils";

export type BannerProps = {
  /** `neutral` is `--raised`; `error` is `--no-soft` with `--no-text`. */
  tone?: "neutral" | "error";
  /** The icon in front. A banner always has one: an info or an alert icon by default. */
  icon?: LucideIcon;
  /**
   * An illustration slot in place of the icon: the alarm clock of a closed market that
   * waits for its result (DESIGN.md §13).
   */
  illustration?: IllustrationName;
  children: ReactNode;
  /** A control that belongs to the banner, shown after its text: "Try again". */
  action?: ReactNode;
  className?: string;
};

/**
 * A banner (DESIGN.md §12): a condition that lasts, such as a paused market or an API
 * that is down. Full width inside the content column, with an icon. A screen shows at
 * most one.
 */
export function Banner({
  tone = "neutral",
  icon,
  illustration,
  children,
  action,
  className,
}: BannerProps) {
  const Icon = icon ?? (tone === "error" ? TriangleAlertIcon : InfoIcon);
  const hasAction = action !== undefined && action !== null && action !== false;

  return (
    <Alert variant={tone} role={tone === "error" ? "alert" : "status"} className={className}>
      {illustration !== undefined ? (
        <Illustration name={illustration} className="mt-px size-5 text-current" />
      ) : (
        <Icon aria-hidden />
      )}
      <AlertDescription className={cn(hasAction && "flex flex-col items-start gap-3")}>
        <div>{children}</div>
        {hasAction && action}
      </AlertDescription>
    </Alert>
  );
}

export type ErrorBannerProps = {
  error: ApiError;
  /** Asks again. Leave it out where asking again makes no sense. */
  onRetry?: () => void;
  className?: string;
};

/**
 * The error state of a screen (DESIGN.md §14): the API's message as sent, and "Try
 * again". After `RATE_LIMITED` the button is disabled for the time the API names and
 * counts it down.
 */
export function ErrorBanner({ error, onRetry, className }: ErrorBannerProps) {
  const wait = useRetryAfter(error);

  return (
    <Banner
      tone="error"
      className={className}
      action={
        onRetry !== undefined && (
          <Button
            variant="secondary"
            size="compact"
            width="fit"
            disabled={wait > 0}
            onClick={onRetry}
          >
            {wait > 0 ? `Try again in ${wait} s` : "Try again"}
          </Button>
        )
      }
    >
      {messageOf(error)}
    </Banner>
  );
}
