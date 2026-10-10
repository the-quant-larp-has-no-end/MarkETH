import { screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, test, vi } from "vitest";

import { Button } from "@/components/ui/button";
import { setViewport } from "@/test/media";
import { renderWithProviders } from "@/test/render";

import { ResponsiveSheet, SheetBody, SheetFooter } from "./responsive-sheet";

function Example({
  dismissible,
  hideTitle,
  onOpenChange,
}: {
  dismissible?: boolean;
  hideTitle?: boolean;
  onOpenChange?: (open: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  const change = (next: boolean) => {
    setOpen(next);
    onOpenChange?.(next);
  };
  return (
    <>
      <button type="button" onClick={() => change(true)}>
        Redeem code
      </button>
      <ResponsiveSheet
        open={open}
        onOpenChange={change}
        title="Redeem code"
        hideTitle={hideTitle}
        dismissible={dismissible}
      >
        <SheetBody>
          <label>
            Code <input />
          </label>
        </SheetBody>
        <SheetFooter>
          <Button width="full">Redeem</Button>
        </SheetFooter>
      </ResponsiveSheet>
    </>
  );
}

describe.each([
  ["below 768 px it is the bottom sheet", "phone", "drawer-content"],
  ["from 768 px it is a dialog", "wide", "dialog-content"],
] as const)("ResponsiveSheet: %s", (_name, viewport, slot) => {
  test("opens as a dialog named by its title, with its content and its pinned action", async () => {
    setViewport(viewport);
    const { user } = renderWithProviders(<Example />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Redeem code" }));

    const sheet = await screen.findByRole("dialog", { name: "Redeem code" });
    expect(sheet).toHaveAttribute("data-slot", slot);
    expect(sheet).toHaveClass("bg-card");
    expect(screen.getByRole("heading", { name: "Redeem code" })).toBeInTheDocument();
    expect(screen.getByLabelText("Code")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Redeem" })).toBeInTheDocument();
  });

  test("closes with the close button and with Escape", async () => {
    setViewport(viewport);
    const onOpenChange = vi.fn();
    const { user } = renderWithProviders(<Example onOpenChange={onOpenChange} />);

    await user.click(screen.getByRole("button", { name: "Redeem code" }));
    await user.click(await screen.findByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(onOpenChange).toHaveBeenLastCalledWith(false);

    await user.click(screen.getByRole("button", { name: "Redeem code" }));
    await screen.findByRole("dialog");
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  test("stays when it is told that it must: no close button, and Escape does nothing", async () => {
    setViewport(viewport);
    const { user } = renderWithProviders(<Example dismissible={false} />);

    await user.click(screen.getByRole("button", { name: "Redeem code" }));
    await screen.findByRole("dialog");

    expect(screen.queryByRole("button", { name: "Close" })).not.toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  test("can keep its title for screen readers only", async () => {
    setViewport(viewport);
    const { user } = renderWithProviders(<Example hideTitle />);

    await user.click(screen.getByRole("button", { name: "Redeem code" }));

    expect(await screen.findByRole("dialog", { name: "Redeem code" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Redeem code" })).toHaveClass("sr-only");
  });
});

test("the bottom sheet carries its handle; the dialog does not", async () => {
  const { user, unmount } = renderWithProviders(<Example />);
  await user.click(screen.getByRole("button", { name: "Redeem code" }));
  await screen.findByRole("dialog");
  expect(document.querySelector("[data-slot=drawer-handle]")).toBeInTheDocument();
  unmount();

  setViewport("wide");
  const wide = renderWithProviders(<Example />);
  await wide.user.click(screen.getByRole("button", { name: "Redeem code" }));
  await screen.findByRole("dialog");
  expect(document.querySelector("[data-slot=drawer-handle]")).not.toBeInTheDocument();
});
