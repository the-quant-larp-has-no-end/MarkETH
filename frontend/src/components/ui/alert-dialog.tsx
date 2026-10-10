"use client";

import * as React from "react";
import { AlertDialog as AlertDialogPrimitive } from "radix-ui";

import { cn } from "@/lib/utils";

import { Button } from "./button";
import {
  besideCloseCrossClasses,
  CloseCross,
  closeCrossClasses,
  dialogSurfaceClasses,
  overlayClasses,
} from "./dialog";

/**
 * The confirmation of a destructive or irreversible admin action, and of nothing else
 * (DESIGN.md §12). It states the consequence; it cannot be dismissed by tapping beside it.
 * Its close cross does what "Cancel" does.
 */

function AlertDialog({ ...props }: React.ComponentProps<typeof AlertDialogPrimitive.Root>) {
  return <AlertDialogPrimitive.Root data-slot="alert-dialog" {...props} />;
}

function AlertDialogTrigger({
  ...props
}: React.ComponentProps<typeof AlertDialogPrimitive.Trigger>) {
  return <AlertDialogPrimitive.Trigger data-slot="alert-dialog-trigger" {...props} />;
}

function AlertDialogPortal({ ...props }: React.ComponentProps<typeof AlertDialogPrimitive.Portal>) {
  return <AlertDialogPrimitive.Portal data-slot="alert-dialog-portal" {...props} />;
}

function AlertDialogOverlay({
  className,
  ...props
}: React.ComponentProps<typeof AlertDialogPrimitive.Overlay>) {
  return (
    <AlertDialogPrimitive.Overlay
      data-slot="alert-dialog-overlay"
      className={cn(overlayClasses, className)}
      {...props}
    />
  );
}

function AlertDialogContent({
  className,
  children,
  locked = false,
  onEscapeKeyDown,
  ...props
}: React.ComponentProps<typeof AlertDialogPrimitive.Content> & {
  /**
   * The dialog cannot be closed right now: the request it confirmed is out. Escape does
   * nothing, and the close cross stays in its place, disabled.
   */
  locked?: boolean;
}) {
  return (
    <AlertDialogPortal>
      <AlertDialogOverlay />
      <AlertDialogPrimitive.Content
        data-slot="alert-dialog-content"
        className={cn(dialogSurfaceClasses, "gap-4 overflow-y-auto p-4", className)}
        {...props}
        onEscapeKeyDown={(event) => {
          if (locked) event.preventDefault();
          onEscapeKeyDown?.(event);
        }}
      >
        {children}
        {/*
          The primitive's `Action` is its plain closing button. Closing is what "Cancel"
          does, and unlike `Cancel` this one does not take the focus when the dialog
          opens: that stays with the button that says "Cancel".
        */}
        <AlertDialogPrimitive.Action
          data-slot="alert-dialog-close"
          aria-label="Close"
          disabled={locked}
          className={closeCrossClasses}
        >
          <CloseCross />
        </AlertDialogPrimitive.Action>
      </AlertDialogPrimitive.Content>
    </AlertDialogPortal>
  );
}

function AlertDialogHeader({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="alert-dialog-header"
      className={cn("flex flex-col gap-2", besideCloseCrossClasses, className)}
      {...props}
    />
  );
}

function AlertDialogFooter({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="alert-dialog-footer"
      className={cn("flex flex-col-reverse gap-2 md:flex-row md:justify-end", className)}
      {...props}
    />
  );
}

function AlertDialogTitle({
  className,
  ...props
}: React.ComponentProps<typeof AlertDialogPrimitive.Title>) {
  return (
    <AlertDialogPrimitive.Title
      data-slot="alert-dialog-title"
      className={cn("type-h2", className)}
      {...props}
    />
  );
}

function AlertDialogDescription({
  className,
  ...props
}: React.ComponentProps<typeof AlertDialogPrimitive.Description>) {
  return (
    <AlertDialogPrimitive.Description
      data-slot="alert-dialog-description"
      className={cn("type-body-sm text-muted-foreground", className)}
      {...props}
    />
  );
}

/**
 * The button that goes ahead. It closes the dialog when tapped; for an action that waits
 * for the API and may be refused, use a plain `Button` and close the dialog on success,
 * so that the API's message can be shown inside it.
 */
function AlertDialogAction({
  variant = "primary",
  size = "default",
  ...props
}: React.ComponentProps<typeof AlertDialogPrimitive.Action> &
  Pick<React.ComponentProps<typeof Button>, "variant" | "size">) {
  return (
    <Button variant={variant} size={size} asChild>
      <AlertDialogPrimitive.Action data-slot="alert-dialog-action" {...props} />
    </Button>
  );
}

function AlertDialogCancel({
  variant = "ghost",
  size = "default",
  ...props
}: React.ComponentProps<typeof AlertDialogPrimitive.Cancel> &
  Pick<React.ComponentProps<typeof Button>, "variant" | "size">) {
  return (
    <Button variant={variant} size={size} asChild>
      <AlertDialogPrimitive.Cancel data-slot="alert-dialog-cancel" {...props} />
    </Button>
  );
}

export {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogOverlay,
  AlertDialogPortal,
  AlertDialogTitle,
  AlertDialogTrigger,
};
