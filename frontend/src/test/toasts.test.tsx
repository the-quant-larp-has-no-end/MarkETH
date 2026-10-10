import { render, screen } from "@testing-library/react";
import { toast } from "sonner";
import { describe, expect, test } from "vitest";

import { Toaster } from "@/components/ui/sonner";

/** The id two tests in a row give their toast, as two tests that tell the same notice do. */
const SAME = "0e6d4a52-7c1b-4f39-a2d8-5b9c3e1f7a60";

const frame = () => new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
const toastOf = (text: HTMLElement) => text.closest<HTMLElement>("[data-sonner-toast]")!;

/**
 * The two tests are one case, and their order is the case: what the first leaves on its
 * way must be over when the second starts (`clearToasts`, which the set-up calls after
 * every test).
 */
describe("a toast that is put away as its test ends", () => {
  test("first: a toast with an id of its own is put away, and the test is over at once", async () => {
    render(<Toaster />);
    toast("Saved", { id: SAME });
    await screen.findByText("Saved");

    // sonner tells its Toasters of this one animation frame later.
    toast.dismiss(SAME);
  });

  test("then: the next test's toast with that id stays up, and sonner knows it is up", async () => {
    render(<Toaster />);
    // The frame in which the news of the first test would arrive.
    await frame();

    toast("Saved", { id: SAME });
    const shown = toastOf(await screen.findByText("Saved"));
    // The frames in which that news would put the toast away.
    await frame();
    await frame();

    expect(shown).toBeInTheDocument();
    expect(shown).toHaveAttribute("data-removed", "false");
    expect(toast.getToasts().map((held) => held.id)).toEqual([SAME]);
  });

  test("last: a Toaster that mounts when every toast was put away shows none", async () => {
    // The toast of the test before was up when that test ended.
    render(<Toaster />);
    await frame();
    await frame();

    expect(screen.queryByText("Saved")).not.toBeInTheDocument();
    expect(toast.getToasts()).toEqual([]);
  });
});
