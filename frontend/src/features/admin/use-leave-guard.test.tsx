import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";

import { Link } from "@/components/shell";
import { navigation } from "@/test/navigation";
import { renderWithProviders } from "@/test/render";

import { useLeaveGuard } from "./use-leave-guard";

function Page({ guarded }: { guarded: boolean }) {
  const guard = useLeaveGuard(guarded);
  return (
    <>
      <Link href="/admin/risk?tab=alerts">Risk</Link>
      <Link href="/admin/codes">Codes</Link>
      <a href="https://example.org/elsewhere">Another site</a>
      <a href="/admin/risk" target="_blank" rel="noreferrer">
        Risk in a new tab
      </a>
      <a href="blob:http://localhost:3000/file" download="codes.csv">
        A file
      </a>
      <output>
        {guard.leaving === null
          ? "nothing"
          : JSON.stringify(
              guard.leaving.by === "link"
                ? { ...guard.leaving, from: guard.leaving.from.textContent }
                : guard.leaving,
            )}
      </output>
      <button type="button" onClick={guard.stay}>
        Stay
      </button>
      <button type="button" onClick={guard.leave}>
        Leave
      </button>
    </>
  );
}

const held = () => screen.getByRole("status").textContent;

/** What a browser does when a way out of the page is taken: it asks the page first. */
function unload(): boolean {
  const event = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(event);
  return event.defaultPrevented;
}

/** The user's own step back, as the page hears of it. */
function back(): void {
  act(() => {
    window.dispatchEvent(new PopStateEvent("popstate"));
  });
}

describe("the leave guard", () => {
  let pushState: ReturnType<typeof vi.spyOn>;
  let goBack: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    navigation.go("/admin/codes");
    // The history is watched, not walked. As in a browser, a step back that was asked for
    // is heard of a moment later.
    pushState = vi.spyOn(window.history, "pushState").mockImplementation(() => {});
    goBack = vi.spyOn(window.history, "back").mockImplementation(() => {
      queueMicrotask(() => window.dispatchEvent(new PopStateEvent("popstate")));
    });
  });

  test("while it is off, nothing is held and the history is left alone", async () => {
    const { user } = renderWithProviders(<Page guarded={false} />);

    expect(unload()).toBe(false);
    expect(pushState).not.toHaveBeenCalled();

    await user.click(screen.getByRole("link", { name: "Risk" }));

    expect(navigation.href).toBe("/admin/risk?tab=alerts");
    expect(held()).toBe("nothing");
  });

  test("closing the tab, reloading or going to another site makes the browser ask", () => {
    const { rerender } = renderWithProviders(<Page guarded />);

    expect(unload()).toBe(true);

    rerender(<Page guarded={false} />);
    expect(unload()).toBe(false);
  });

  test("a link inside the app is held back, and the guard says where it wanted to go", async () => {
    const { user } = renderWithProviders(<Page guarded />);

    await user.click(screen.getByRole("link", { name: "Risk" }));

    expect(navigation.href).toBe("/admin/codes");
    expect(navigation.router.push).not.toHaveBeenCalled();
    expect(held()).toBe(
      JSON.stringify({ by: "link", href: "/admin/risk?tab=alerts", from: "Risk" }),
    );
  });

  test("staying keeps the page, and the link is held again the next time", async () => {
    const { user } = renderWithProviders(<Page guarded />);
    await user.click(screen.getByRole("link", { name: "Risk" }));

    await user.click(screen.getByRole("button", { name: "Stay" }));

    expect(held()).toBe("nothing");
    expect(navigation.href).toBe("/admin/codes");
    expect(navigation.router.replace).not.toHaveBeenCalled();

    await user.click(screen.getByRole("link", { name: "Risk" }));
    expect(held()).toContain("/admin/risk");
  });

  test("leaving goes where the link led, in place of the guard's own history entry", async () => {
    const { user } = renderWithProviders(<Page guarded />);
    await user.click(screen.getByRole("link", { name: "Risk" }));

    await user.click(screen.getByRole("button", { name: "Leave" }));

    expect(navigation.router.replace).toHaveBeenCalledWith("/admin/risk?tab=alerts");
    expect(navigation.href).toBe("/admin/risk?tab=alerts");
    expect(held()).toBe("nothing");
    expect(goBack).not.toHaveBeenCalled();
  });

  test("a link to the page one is on leads nowhere and asks nothing", async () => {
    const { user } = renderWithProviders(<Page guarded />);

    await user.click(screen.getByRole("link", { name: "Codes" }));

    expect(held()).toBe("nothing");
    expect(navigation.router.push).not.toHaveBeenCalled();
  });

  test.each(["Another site", "Risk in a new tab", "A file"])(
    "a link that does not take this page away passes: %s",
    (name) => {
      renderWithProviders(<Page guarded />);
      const link = screen.getByRole("link", { name });
      // The browser would follow it; the test only looks at whether the guard held it.
      const reached = vi.fn((event: Event) => event.preventDefault());
      link.addEventListener("click", reached);

      fireEvent.click(link);

      expect(reached).toHaveBeenCalledTimes(1);
      expect(held()).toBe("nothing");
    },
  );

  test("a tap with a modifier opens the link beside the page and passes", () => {
    renderWithProviders(<Page guarded />);

    fireEvent.click(screen.getByRole("link", { name: "Risk" }), { ctrlKey: true });

    expect(held()).toBe("nothing");
  });

  test("it keeps an entry of its own on top of the history, so one step back stays on the page and is held", () => {
    renderWithProviders(<Page guarded />);
    expect(pushState).toHaveBeenCalledTimes(1);
    expect(pushState).toHaveBeenLastCalledWith(window.history.state, "", window.location.href);

    back();

    expect(held()).toBe(JSON.stringify({ by: "back" }));
    expect(goBack).not.toHaveBeenCalled();
  });

  test("staying after a step back guards the page again", async () => {
    const { user } = renderWithProviders(<Page guarded />);
    back();

    await user.click(screen.getByRole("button", { name: "Stay" }));

    expect(held()).toBe("nothing");
    expect(pushState).toHaveBeenCalledTimes(2);

    back();
    expect(held()).toBe(JSON.stringify({ by: "back" }));
  });

  test("leaving after a step back takes the step that was meant, and that one is not held", async () => {
    const { user } = renderWithProviders(<Page guarded />);
    back();

    await user.click(screen.getByRole("button", { name: "Leave" }));

    // The step was taken, and when it arrived it was not read as another wish to leave.
    expect(goBack).toHaveBeenCalledTimes(1);
    expect(held()).toBe("nothing");
  });

  test("switching it off takes its entry off the history again, without holding that step", async () => {
    const { rerender } = renderWithProviders(<Page guarded />);

    // Off, and on again while the step back that took the entry off is still on its way.
    rerender(<Page guarded={false} />);
    rerender(<Page guarded />);
    expect(goBack).toHaveBeenCalledTimes(1);
    await act(async () => {});
    expect(held()).toBe("nothing");

    // The next one is the user's.
    back();
    await waitFor(() => expect(held()).toBe(JSON.stringify({ by: "back" })));
  });

  test("its entry is left alone once the page has moved on from it", async () => {
    const { user, unmount } = renderWithProviders(<Page guarded />);
    await user.click(screen.getByRole("link", { name: "Risk" }));
    await user.click(screen.getByRole("button", { name: "Leave" }));

    unmount();

    expect(goBack).not.toHaveBeenCalled();
  });
});
