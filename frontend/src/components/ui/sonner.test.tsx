import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { toast } from "sonner";
import { describe, expect, test } from "vitest";

import { TOAST_MS } from "@/lib/design";

import { Toaster } from "./sonner";

describe("a toast", () => {
  test("confirms a finished action and has a close cross at its right end, in a 44 px target", async () => {
    render(<Toaster />);

    toast("Profile saved");

    expect(await screen.findByText("Profile saved")).toBeInTheDocument();
    const cross = screen.getByRole("button", { name: "Close" });
    expect(cross.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    // sonner styles its close button itself: these classes insist on the 44 px target.
    expect(cross).toHaveClass("size-11!", "top-1/2!");
    const toaster = document.querySelector<HTMLElement>("[data-sonner-toaster]")!;
    expect(toaster.style.getPropertyValue("--toast-close-button-end")).toBe("0.25rem");
    expect(toaster.style.getPropertyValue("--toast-close-button-start")).toBe("auto");
    // The text keeps clear of the cross.
    expect(document.querySelector("[data-sonner-toast]")).toHaveClass("pr-13!");
  });

  test("goes when its cross is tapped, before its 4 s are over", async () => {
    const user = userEvent.setup();
    render(<Toaster />);
    toast("Role saved");
    await screen.findByText("Role saved");

    await user.click(screen.getByRole("button", { name: "Close" }));

    await waitFor(() => expect(screen.queryByText("Role saved")).not.toBeInTheDocument(), {
      timeout: TOAST_MS - 1000,
    });
  });
});
