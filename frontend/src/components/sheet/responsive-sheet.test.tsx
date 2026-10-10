import { act, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, test, vi } from "vitest";

import { Button } from "@/components/ui/button";
import { setViewport } from "@/test/media";
import { renderWithProviders } from "@/test/render";

import { ResponsiveSheet, SheetBody, SheetFooter } from "./responsive-sheet";

function Example({
  dismissible,
  hideTitle,
  header,
  dialog,
  onOpenChange,
}: {
  dismissible?: boolean;
  hideTitle?: boolean;
  header?: string;
  dialog?: boolean;
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
        header={header}
        dismissible={dismissible}
        dialog={dialog}
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
    // The title keeps 8 px of its own below it and the body starts that much higher: what
    // scrolls is cut below the title, and title and content are 16 px apart.
    expect(screen.getByRole("heading", { name: "Redeem code" }).parentElement).toHaveClass("pb-2");
    expect(sheet).toHaveClass("[--sheet-body-top:0.5rem]");
    expect(document.querySelector("[data-slot=sheet-body]")).toHaveClass(
      "px-4",
      "pb-4",
      "pt-[var(--sheet-body-top,1rem)]",
    );
    expect(screen.getByLabelText("Code")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Redeem" })).toBeInTheDocument();
  });

  test("has the close cross at its top right: 24 px in a 44 px target, 4 px from the top and the right edge", async () => {
    setViewport(viewport);
    const { user } = renderWithProviders(<Example />);

    await user.click(screen.getByRole("button", { name: "Redeem code" }));
    const sheet = await screen.findByRole("dialog");

    const cross = screen.getByRole("button", { name: "Close" });
    expect(sheet).toContainElement(cross);
    expect(cross).toBeEnabled();
    expect(cross).toHaveClass("absolute", "top-1", "right-1", "size-11", "text-foreground");
    expect(cross.querySelector("svg")).toHaveClass("size-6");
    // The cross is the last stop of the Tab key: what opens first is not a way out.
    expect(Array.from(sheet.querySelectorAll("button")).at(-1)).toBe(cross);
  });

  test("the title ends 48 px before the right edge, where the cross stands", async () => {
    setViewport(viewport);
    const { user } = renderWithProviders(<Example />);

    await user.click(screen.getByRole("button", { name: "Redeem code" }));
    await screen.findByRole("dialog");

    const header = screen.getByRole("heading", { name: "Redeem code" }).parentElement;
    // 16 px of padding and 32 px more, or 48 px of padding: the same 48 px either way.
    expect(header?.className).toMatch(
      viewport === "wide" ? /\bpx-4\b.*\bmr-8\b|\bmr-8\b.*\bpx-4\b/ : /\bpr-12\b/,
    );
    expect(header).toHaveClass("shrink-0");
  });

  test("the pinned buttons stand under a 1 px line that shows where the scrolling part ends", async () => {
    setViewport(viewport);
    const { user } = renderWithProviders(<Example />);

    await user.click(screen.getByRole("button", { name: "Redeem code" }));
    await screen.findByRole("dialog");

    const footer = document.querySelector("[data-slot=sheet-footer]");
    expect(footer).toHaveClass("border-t", "shrink-0");
    expect(footer).toContainElement(screen.getByRole("button", { name: "Redeem" }));
  });

  test("closes with the close cross and with Escape", async () => {
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

  test("stays when it is told that it must: the close cross is there but disabled, and Escape does nothing", async () => {
    setViewport(viewport);
    const onOpenChange = vi.fn();
    const { user } = renderWithProviders(
      <Example dismissible={false} onOpenChange={onOpenChange} />,
    );

    await user.click(screen.getByRole("button", { name: "Redeem code" }));
    await screen.findByRole("dialog");
    onOpenChange.mockClear();

    // Never taken away: it stays in its place, in the look of a disabled button.
    const cross = screen.getByRole("button", { name: "Close" });
    expect(cross).toBeDisabled();
    expect(cross).toHaveClass("absolute", "top-1", "right-1", "disabled:text-subtle");
    await user.click(cross);
    await user.keyboard("{Escape}");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(onOpenChange).not.toHaveBeenCalled();
  });

  test("can keep its title for screen readers only: the header row still stands, as tall as the cross", async () => {
    setViewport(viewport);
    const { user } = renderWithProviders(<Example hideTitle />);

    await user.click(screen.getByRole("button", { name: "Redeem code" }));

    const sheet = await screen.findByRole("dialog", { name: "Redeem code" });
    expect(screen.getByRole("heading", { name: "Redeem code" })).toHaveClass("sr-only");
    // Nothing lies under the cross and nothing scrolls under it: the row above the body
    // does not scroll, and reaches down as far as the cross does.
    const row = sheet.querySelector("[data-slot=sheet-header]");
    expect(row).toHaveClass("shrink-0", "pr-12", viewport === "wide" ? "min-h-12" : "min-h-9");
    expect(row).toBeEmptyDOMElement();
    expect(row?.compareDocumentPosition(document.querySelector("[data-slot=sheet-body]")!)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
    expect(screen.getByRole("button", { name: "Close" })).toBeEnabled();
  });

  test("with a hidden title, what says what the sheet is about stands in the header row beside the cross", async () => {
    setViewport(viewport);
    const { user } = renderWithProviders(
      <Example hideTitle header="Will the Polybahn run without interruption?" />,
    );

    await user.click(screen.getByRole("button", { name: "Redeem code" }));
    const sheet = await screen.findByRole("dialog", { name: "Redeem code" });

    const row = sheet.querySelector("[data-slot=sheet-header]");
    expect(row).toHaveTextContent("Will the Polybahn run without interruption?");
    // It ends 48 px before the right edge, and gives way before it reaches the cross.
    expect(row).toHaveClass("pr-12");
    expect(row?.firstElementChild).toHaveClass("min-w-0", "flex-1");
  });

  test("takes the focus when it opens, keeps the Tab key inside, and gives the focus back to what opened it", async () => {
    setViewport(viewport);
    const { user } = renderWithProviders(<Example />);
    const opener = screen.getByRole("button", { name: "Redeem code" });

    await user.click(opener);
    const sheet = await screen.findByRole("dialog", { name: "Redeem code" });

    await waitFor(() => expect(sheet.contains(document.activeElement)).toBe(true));
    for (let press = 0; press < 5; press += 1) {
      await user.tab();
      expect(sheet.contains(document.activeElement)).toBe(true);
    }

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(opener).toHaveFocus());
  });

  test("gives the focus back after the close button too, and after every opening anew", async () => {
    setViewport(viewport);
    const { user } = renderWithProviders(
      <>
        <button type="button">Another control</button>
        <Example />
      </>,
    );
    const opener = screen.getByRole("button", { name: "Redeem code" });

    for (let opening = 0; opening < 2; opening += 1) {
      await user.click(opener);
      await user.click(await screen.findByRole("button", { name: "Close" }));
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      await waitFor(() => expect(opener).toHaveFocus());
      screen.getByRole("button", { name: "Another control" }).focus();
    }
  });
});

test("the bottom sheet takes the focus itself, so that no keyboard slides up; the dialog takes it to its first control", async () => {
  const phone = renderWithProviders(<Example />);
  await phone.user.click(screen.getByRole("button", { name: "Redeem code" }));
  const sheet = await screen.findByRole("dialog");
  await waitFor(() => expect(sheet).toHaveFocus());
  expect(screen.getByLabelText("Code")).not.toHaveFocus();
  phone.unmount();

  setViewport("wide");
  const wide = renderWithProviders(<Example />);
  await wide.user.click(screen.getByRole("button", { name: "Redeem code" }));
  await screen.findByRole("dialog");
  await waitFor(() => expect(screen.getByLabelText("Code")).toHaveFocus());
});

test("turning the phone while the sheet is open keeps the focus in it, and it still goes back to the opener", async () => {
  const { user } = renderWithProviders(<Example />);
  const opener = screen.getByRole("button", { name: "Redeem code" });
  await user.click(opener);
  expect(await screen.findByRole("dialog")).toHaveAttribute("data-slot", "drawer-content");

  act(() => setViewport("wide"));

  const dialog = await screen.findByRole("dialog");
  expect(dialog).toHaveAttribute("data-slot", "dialog-content");
  // After the bottom sheet has had its say about the focus, too.
  await act(() => new Promise((resolve) => setTimeout(resolve, 20)));
  expect(dialog.contains(document.activeElement)).toBe(true);

  await user.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  await waitFor(() => expect(opener).toHaveFocus());
});

test("the blocks of the body keep their height: a short sheet scrolls instead of squeezing them", async () => {
  const { user } = renderWithProviders(<Example />);
  await user.click(screen.getByRole("button", { name: "Redeem code" }));
  await screen.findByRole("dialog");

  expect(document.querySelector("[data-slot=sheet-body]")).toHaveClass(
    "overflow-y-auto",
    "*:shrink-0",
  );
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

test("can be a dialog at every width: on a phone it is the centred dialog, with its cross and its footer line", async () => {
  const { user } = renderWithProviders(<Example dialog />);

  await user.click(screen.getByRole("button", { name: "Redeem code" }));

  const dialog = await screen.findByRole("dialog", { name: "Redeem code" });
  expect(dialog).toHaveAttribute("data-slot", "dialog-content");
  // 420 px wide, never wider than the viewport less 16 px on each side, at most 90% tall.
  expect(dialog).toHaveClass("max-w-[26.25rem]", "w-[calc(100%-2rem)]", "max-h-[90dvh]");
  expect(document.querySelector("[data-slot=drawer-handle]")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Close" })).toBeEnabled();
  expect(document.querySelector("[data-slot=sheet-footer]")).toHaveClass("border-t");
  // The dialog takes the focus to its first control.
  await waitFor(() => expect(screen.getByLabelText("Code")).toHaveFocus());
});

test("a dialog that must stay does not close on a tap beside it either", async () => {
  setViewport("wide");
  const { user } = renderWithProviders(<Example dismissible={false} />);
  await user.click(screen.getByRole("button", { name: "Redeem code" }));
  await screen.findByRole("dialog");

  await user.click(document.querySelector("[data-slot=dialog-overlay]")!);

  expect(screen.getByRole("dialog")).toBeInTheDocument();
});

test("a dialog that may go closes on a tap beside it", async () => {
  setViewport("wide");
  const { user } = renderWithProviders(<Example />);
  await user.click(screen.getByRole("button", { name: "Redeem code" }));
  await screen.findByRole("dialog");

  await user.click(document.querySelector("[data-slot=dialog-overlay]")!);

  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
});

/** A sheet that holds what opens a second one, as the filter sheet holds a picker. */
function Stacked({ dialog }: { dialog?: boolean }) {
  const [upper, setUpper] = useState(false);
  return (
    <ResponsiveSheet open onOpenChange={() => {}} title="Filter" dialog={dialog}>
      <SheetBody>
        <button type="button" onClick={() => setUpper(true)}>
          Choose a programme
        </button>
      </SheetBody>
      <ResponsiveSheet open={upper} onOpenChange={setUpper} title="Programme">
        <SheetBody>
          <p>The programmes</p>
        </SheetBody>
      </ResponsiveSheet>
    </ResponsiveSheet>
  );
}

describe("a sheet inside a sheet", () => {
  test("below 768 px it is nested in the bottom sheet under it, which stays open when it closes", async () => {
    const { user } = renderWithProviders(<Stacked />);
    const under = await screen.findByRole("dialog", { name: "Filter" });

    await user.click(screen.getByRole("button", { name: "Choose a programme" }));

    const upper = await screen.findByRole("dialog", { name: "Programme" });
    expect(upper).toHaveAttribute("data-slot", "drawer-content");
    expect(screen.getByText("The programmes")).toBeInTheDocument();
    // The upper sheet takes the focus to itself, as every bottom sheet does.
    await waitFor(() => expect(upper).toHaveFocus());

    await user.keyboard("{Escape}");

    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "Programme" })).not.toBeInTheDocument(),
    );
    expect(screen.getByRole("dialog", { name: "Filter" })).toBe(under);
    // The sheet under it hears that the upper one is gone and stands at its full size.
    expect(under.style.transform).toContain("scale(1)");
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Choose a programme" })).toHaveFocus(),
    );
  });

  test("the close cross of the upper sheet closes it alone", async () => {
    const { user } = renderWithProviders(<Stacked />);
    await screen.findByRole("dialog", { name: "Filter" });
    await user.click(screen.getByRole("button", { name: "Choose a programme" }));
    const upper = await screen.findByRole("dialog", { name: "Programme" });

    await user.click(within(upper).getByRole("button", { name: "Close" }));

    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "Programme" })).not.toBeInTheDocument(),
    );
    expect(screen.getByRole("dialog", { name: "Filter" })).toBeInTheDocument();
  });

  test("inside a dialog it is a bottom sheet of its own", async () => {
    const { user } = renderWithProviders(<Stacked dialog />);
    const under = await screen.findByRole("dialog", { name: "Filter" });
    expect(under).toHaveAttribute("data-slot", "dialog-content");

    await user.click(screen.getByRole("button", { name: "Choose a programme" }));

    expect(await screen.findByRole("dialog", { name: "Programme" })).toHaveAttribute(
      "data-slot",
      "drawer-content",
    );

    await user.keyboard("{Escape}");

    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "Programme" })).not.toBeInTheDocument(),
    );
    expect(screen.getByRole("dialog", { name: "Filter" })).toBeInTheDocument();
  });
});

/** A dialog that holds a combobox, as the filter sheet holds a picker from 768 px. */
function WithList({ expanded }: { expanded: boolean }) {
  const [open, setOpen] = useState(true);
  return (
    <ResponsiveSheet open={open} onOpenChange={setOpen} title="Filter">
      <SheetBody>
        <input aria-label="Level" role="combobox" aria-expanded={expanded} aria-controls="levels" />
      </SheetBody>
    </ResponsiveSheet>
  );
}

describe("a dialog that holds a picker whose list is open", () => {
  test("Escape is the list's: the dialog stays", async () => {
    setViewport("wide");
    const { user } = renderWithProviders(<WithList expanded />);
    await screen.findByRole("dialog", { name: "Filter" });
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Level" })).toHaveFocus());

    await user.keyboard("{Escape}");

    expect(screen.getByRole("dialog", { name: "Filter" })).toBeInTheDocument();
  });

  test("so is a tap beside the dialog", async () => {
    setViewport("wide");
    const { user } = renderWithProviders(<WithList expanded />);
    await screen.findByRole("dialog", { name: "Filter" });

    await user.click(document.querySelector("[data-slot=dialog-overlay]")!);

    expect(screen.getByRole("dialog", { name: "Filter" })).toBeInTheDocument();
  });

  test("with the list closed, Escape and a tap beside the dialog close it", async () => {
    setViewport("wide");
    const first = renderWithProviders(<WithList expanded={false} />);
    await screen.findByRole("dialog", { name: "Filter" });
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Level" })).toHaveFocus());

    await first.user.keyboard("{Escape}");

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    first.unmount();

    const second = renderWithProviders(<WithList expanded={false} />);
    await screen.findByRole("dialog", { name: "Filter" });

    await second.user.click(document.querySelector("[data-slot=dialog-overlay]")!);

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
});
