import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, test, vi } from "vitest";

import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "./alert-dialog";
import { Button } from "./button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "./dialog";

/** A dialog that a screen opens by state, as every dialog of the app is. */
function Plain({
  locked,
  onOpenChange,
}: {
  locked?: boolean;
  onOpenChange?: (open: boolean) => void;
}) {
  const [open, setOpen] = useState(true);
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        onOpenChange?.(next);
      }}
    >
      <DialogContent locked={locked}>
        <DialogHeader>
          <DialogTitle>Halt market</DialogTitle>
          <DialogDescription>Trading is paused until the market is resumed.</DialogDescription>
        </DialogHeader>
        <Button>Halt</Button>
      </DialogContent>
    </Dialog>
  );
}

function Confirmation({
  locked,
  onOpenChange,
}: {
  locked?: boolean;
  onOpenChange?: (open: boolean) => void;
}) {
  const [open, setOpen] = useState(true);
  return (
    <AlertDialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        onOpenChange?.(next);
      }}
    >
      <AlertDialogContent locked={locked}>
        <AlertDialogHeader>
          <AlertDialogTitle>Void market</AlertDialogTitle>
          <AlertDialogDescription>Every trader gets their net stake back.</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={locked}>Cancel</AlertDialogCancel>
          <Button variant="destructive">Void</Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

const cross = (dialog: HTMLElement) => within(dialog).getByRole("button", { name: "Close" });

describe("Dialog", () => {
  test("has the close cross at its top right: 24 px in a 44 px target, 4 px from the top and the right edge", async () => {
    render(<Plain />);
    const dialog = await screen.findByRole("dialog", { name: "Halt market" });

    expect(cross(dialog)).toHaveClass("absolute", "top-1", "right-1", "size-11", "text-foreground");
    expect(cross(dialog).querySelector("svg")).toHaveClass("size-6");
    expect(cross(dialog).querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    // The title keeps clear of it: 16 px of the dialog's padding, and 32 px of its own.
    expect(dialog).toHaveClass("p-4");
    expect(screen.getByRole("heading", { name: "Halt market" }).parentElement).toHaveClass("mr-8");
  });

  test("opens on its first control, not on the cross, which is the last stop of the Tab key", async () => {
    render(<Plain />);
    const dialog = await screen.findByRole("dialog");

    await waitFor(() => expect(within(dialog).getByRole("button", { name: "Halt" })).toHaveFocus());
    expect(Array.from(dialog.querySelectorAll("button")).at(-1)).toBe(cross(dialog));
  });

  test("the cross, Escape and a tap beside the dialog all close it", async () => {
    const user = userEvent.setup();
    for (const leave of [
      () => user.click(cross(screen.getByRole("dialog"))),
      () => user.keyboard("{Escape}"),
      () => user.click(document.querySelector("[data-slot=dialog-overlay]")!),
    ]) {
      const onOpenChange = vi.fn();
      const { unmount } = render(<Plain onOpenChange={onOpenChange} />);
      await screen.findByRole("dialog");

      await leave();

      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      expect(onOpenChange).toHaveBeenLastCalledWith(false);
      unmount();
    }
  });

  test("while it is locked nothing closes it, and the cross stays in its place, disabled", async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    render(<Plain locked onOpenChange={onOpenChange} />);
    const dialog = await screen.findByRole("dialog");

    expect(cross(dialog)).toBeDisabled();
    expect(cross(dialog)).toHaveClass("absolute", "top-1", "right-1", "disabled:text-subtle");
    await user.click(cross(dialog));
    await user.keyboard("{Escape}");
    await user.click(document.querySelector("[data-slot=dialog-overlay]")!);

    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(onOpenChange).not.toHaveBeenCalled();
  });
});

describe("AlertDialog", () => {
  test("has the close cross too, and its title keeps clear of it", async () => {
    render(<Confirmation />);
    const dialog = await screen.findByRole("alertdialog", { name: "Void market" });

    expect(cross(dialog)).toHaveClass("absolute", "top-1", "right-1", "size-11", "text-foreground");
    expect(cross(dialog).querySelector("svg")).toHaveClass("size-6");
    expect(screen.getByRole("heading", { name: "Void market" }).parentElement).toHaveClass("mr-8");
  });

  test("opens on Cancel, as a confirmation does; the cross does not take the focus", async () => {
    render(<Confirmation />);
    const dialog = await screen.findByRole("alertdialog");

    await waitFor(() =>
      expect(within(dialog).getByRole("button", { name: "Cancel" })).toHaveFocus(),
    );
    expect(cross(dialog)).not.toHaveFocus();
  });

  test("the cross does what Cancel does: it closes the dialog and confirms nothing", async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    render(<Confirmation onOpenChange={onOpenChange} />);
    const dialog = await screen.findByRole("alertdialog");

    await user.click(cross(dialog));

    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(onOpenChange).toHaveBeenCalledTimes(1);
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  test("a tap beside it does not close it: such an action is neither confirmed nor dropped by a stray tap", async () => {
    const user = userEvent.setup();
    render(<Confirmation />);
    await screen.findByRole("alertdialog");

    await user.click(document.querySelector("[data-slot=alert-dialog-overlay]")!);

    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
  });

  test("while it is locked the cross is disabled and Escape does nothing", async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    render(<Confirmation locked onOpenChange={onOpenChange} />);
    const dialog = await screen.findByRole("alertdialog");

    expect(cross(dialog)).toBeDisabled();
    await user.click(cross(dialog));
    await user.keyboard("{Escape}");

    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    expect(onOpenChange).not.toHaveBeenCalled();
  });
});
