import { screen, within } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import type { UserRole } from "@/lib/api/schema";
import { me } from "@/test/fixtures";
import { setViewport } from "@/test/media";
import { navigation } from "@/test/navigation";
import { renderWithProviders } from "@/test/render";

import { AppShell } from "./app-shell";
import { Link } from "./link";
import { NotFoundScreen } from "./not-found-screen";
import { Screen } from "./screen";
import { useHideTabBar, useShellStore } from "./shell-store";
import { tabsFor } from "./tabs";

function renderShell(
  screenProps: Partial<Parameters<typeof Screen>[0]> = {},
  options: Parameters<typeof renderWithProviders>[1] = {},
) {
  return renderWithProviders(
    <AppShell>
      <Screen top="main" title="Wallet" {...screenProps}>
        <p>content</p>
      </Screen>
    </AppShell>,
    options,
  );
}

const tabBar = () => document.querySelector("[data-slot=tab-bar]") as HTMLElement | null;
const topBar = () => document.querySelector("[data-slot=top-bar]") as HTMLElement;
const tabNames = (nav: HTMLElement) =>
  within(nav)
    .getAllByRole("link")
    .map((link) => link.textContent);

describe("the tab bar", () => {
  test.each([
    ["USER", ["Markets", "Portfolio", "Leaderboard", "Wallet"]],
    ["BARISTA", ["Markets", "Portfolio", "Leaderboard", "Wallet", "Scan"]],
    ["ADMIN", ["Markets", "Portfolio", "Leaderboard", "Wallet", "Admin"]],
  ] as const)("shows a %s these tabs", (role, tabs) => {
    renderShell({}, { me: me({ role }) });

    expect(tabNames(tabBar()!)).toEqual(tabs);
  });

  test("never has more than five tabs, whatever the role", () => {
    for (const role of ["USER", "BARISTA", "ADMIN"] satisfies UserRole[]) {
      expect(tabsFor(role).length).toBeLessThanOrEqual(5);
    }
  });

  test("leads to the routes of the app", () => {
    renderShell({}, { me: me({ role: "ADMIN" }) });

    const hrefs = within(tabBar()!)
      .getAllByRole("link")
      .map((link) => link.getAttribute("href"));
    expect(hrefs).toEqual(["/", "/portfolio", "/leaderboard", "/wallet", "/admin"]);
  });

  test.each([
    ["/", "Markets"],
    ["/markets/3f6c2f0e", "Markets"],
    ["/propose", "Markets"],
    ["/portfolio", "Portfolio"],
    ["/leaderboard", "Leaderboard"],
    ["/wallet", "Wallet"],
    ["/admin/risk", "Admin"],
  ])("on %s the %s tab is the current one, in white", (path, tab) => {
    navigation.go(path);
    renderShell({}, { me: me({ role: "ADMIN" }) });

    const current = within(tabBar()!)
      .getAllByRole("link")
      .filter((link) => link.getAttribute("aria-current") === "page");
    expect(current.map((link) => link.textContent)).toEqual([tab]);
    expect(current[0]).toHaveClass("text-foreground");
  });

  test("on a screen that belongs to no tab, none is current", () => {
    navigation.go("/profile");
    renderShell();

    for (const link of within(tabBar()!).getAllByRole("link")) {
      expect(link).not.toHaveAttribute("aria-current");
      expect(link).toHaveClass("text-muted-foreground");
    }
  });

  test("is a navigation landmark that navigates", async () => {
    const { user } = renderShell();

    await user.click(
      within(screen.getByRole("navigation", { name: "Main" })).getByRole("link", {
        name: "Portfolio",
      }),
    );

    expect(navigation.pathname).toBe("/portfolio");
  });

  test("can be taken out of the way by a screen, and comes back", () => {
    function Scanner({ live }: { live: boolean }) {
      useHideTabBar(live);
      return null;
    }
    const { rerender } = renderWithProviders(
      <AppShell>
        <Scanner live={false} />
      </AppShell>,
    );
    expect(tabBar()).toBeInTheDocument();

    rerender(
      <AppShell>
        <Scanner live />
      </AppShell>,
    );
    expect(tabBar()).not.toBeInTheDocument();

    rerender(
      <AppShell>
        <Scanner live={false} />
      </AppShell>,
    );
    expect(tabBar()).toBeInTheDocument();
  });

  test("from 768 px the tabs sit in the top bar and there is no bottom bar", () => {
    setViewport("wide");
    renderShell({}, { me: me({ role: "BARISTA" }) });

    expect(tabBar()).not.toBeInTheDocument();
    const nav = within(topBar()).getByRole("navigation", { name: "Main" });
    expect(tabNames(nav)).toEqual(["Markets", "Portfolio", "Leaderboard", "Wallet", "Scan"]);
    expect(screen.getAllByRole("navigation")).toHaveLength(1);
  });
});

describe("the top bar", () => {
  test("on a main screen shows the logo with name, linking to Markets", () => {
    renderShell();

    const logo = within(topBar()).getByRole("img", { name: "MarkETH" });
    expect(logo.closest("a")).toHaveAttribute("href", "/");
    expect(within(topBar()).queryByRole("button", { name: "Back" })).not.toBeInTheDocument();
  });

  test("shows the balance of /me as a pill that leads to the Wallet, and the profile button", () => {
    renderShell({}, { me: me({ balance_cr: "1250.999999" }) });

    const balance = within(topBar()).getByRole("link", { name: "Wallet, 1,250.99 tokens" });
    expect(balance).toHaveAttribute("href", "/wallet");
    expect(balance).toHaveTextContent("1,250.99");
    expect(within(topBar()).getByRole("link", { name: "Profile" })).toHaveAttribute(
      "href",
      "/profile",
    );
  });

  test("on an inner screen shows a back arrow and the title, and keeps its right side", () => {
    renderShell({ top: "inner", title: "Profile" });

    expect(within(topBar()).getByRole("button", { name: "Back" })).toBeInTheDocument();
    expect(
      within(topBar()).getByRole("heading", { level: 1, name: "Profile" }),
    ).toBeInTheDocument();
    expect(within(topBar()).queryByRole("img", { name: "MarkETH" })).not.toBeInTheDocument();
    expect(within(topBar()).getByRole("link", { name: /^Wallet,/ })).toBeInTheDocument();
    expect(within(topBar()).getByRole("link", { name: "Profile" })).toBeInTheDocument();
  });

  describe("the back arrow", () => {
    afterEach(() => {
      Reflect.deleteProperty(window, "navigation");
    });

    test("goes one step back when the app has been somewhere before", async () => {
      navigation.go("/");
      const { user } = renderShell({ top: "inner", title: "Profile" });
      // The app moved to another route since the page loaded.
      await user.click(within(tabBar()!).getByRole("link", { name: "Wallet" }));
      expect(navigation.pathname).toBe("/wallet");

      await user.click(screen.getByRole("button", { name: "Back" }));

      expect(navigation.router.back).toHaveBeenCalledTimes(1);
      expect(navigation.pathname).toBe("/");
    });

    test("on a screen that was opened directly goes to Markets", async () => {
      navigation.go("/profile");
      const { user } = renderShell({ top: "inner", title: "Profile" });

      await user.click(screen.getByRole("button", { name: "Back" }));

      expect(navigation.router.back).not.toHaveBeenCalled();
      expect(navigation.pathname).toBe("/");
    });

    test("from the admin view of a market that was opened directly goes to Admin", async () => {
      navigation.go("/admin/markets/3f6c2f0e");
      const { user } = renderShell({ top: "inner", backTo: "/admin" });

      await user.click(screen.getByRole("button", { name: "Back" }));

      expect(navigation.pathname).toBe("/admin");
    });

    test("asks the browser whether there is a way back inside the app, where it can say", async () => {
      Object.defineProperty(window, "navigation", {
        configurable: true,
        value: { canGoBack: true },
      });
      navigation.go("/profile");
      const { user } = renderShell({ top: "inner", title: "Profile" });

      await user.click(screen.getByRole("button", { name: "Back" }));

      expect(navigation.router.back).toHaveBeenCalledTimes(1);
    });
  });

  test("on an inner screen the tabs join the top bar from 1024 px", () => {
    setViewport("wide");
    const { unmount } = renderShell({ top: "inner", title: "Propose a market" });
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
    unmount();

    setViewport("desktop");
    renderShell({ top: "inner", title: "Propose a market" });
    expect(within(topBar()).getByRole("navigation", { name: "Main" })).toBeInTheDocument();
  });
});

describe("Screen", () => {
  test("a main screen names itself in a visually hidden h1", () => {
    renderShell({ top: "main", title: "Wallet" });

    const heading = screen.getByRole("heading", { level: 1, name: "Wallet" });
    expect(heading).toHaveClass("sr-only");
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  });

  test("a screen that shows its own h1 passes no title, and there is still only one", () => {
    renderWithProviders(
      <AppShell>
        <Screen top="main">
          <h1>Leaderboard</h1>
        </Screen>
      </AppShell>,
    );

    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  });

  test("puts the content in the main landmark", () => {
    renderShell();

    expect(within(screen.getByRole("main")).getByText("content")).toBeInTheDocument();
  });

  test("keeps what is pinned at the bottom of the screen, above the tab bar, after the content", () => {
    renderShell({ pinned: <button type="button">Buy YES</button> });

    const pinned = document.querySelector("[data-slot=pinned]")!;
    expect(within(pinned as HTMLElement).getByRole("button", { name: "Buy YES" })).toBeVisible();
    expect(pinned).toHaveClass("sticky", "bottom-(--tab-bar-space)");
    expect(screen.getByRole("main").lastElementChild).toBe(pinned);
  });

  test("shows no pinned bar while there is nothing to pin", () => {
    renderShell({ pinned: null });

    expect(document.querySelector("[data-slot=pinned]")).not.toBeInTheDocument();
  });
});

describe("NotFoundScreen", () => {
  test("shows the campus line and a secondary button to the markets, inside the shell", () => {
    renderWithProviders(
      <AppShell>
        <NotFoundScreen />
      </AppShell>,
    );

    expect(
      screen.getByText("This page does not exist. Not even in the HG basement."),
    ).toBeInTheDocument();
    const browse = screen.getByRole("link", { name: "Browse markets" });
    expect(browse).toHaveAttribute("href", "/");
    expect(browse).toHaveClass("border-border-strong");
    // It is a main screen: the logo, and the tab bar.
    expect(within(topBar()).getByRole("img", { name: "MarkETH" })).toBeInTheDocument();
    expect(tabBar()).toBeInTheDocument();
  });
});

describe("the route progress line", () => {
  const progress = () => document.querySelector("[data-slot=route-progress]");

  function Trigger() {
    const store = useShellStore();
    return (
      <button
        type="button"
        onClick={() => {
          const arrived = store.beginNavigation();
          window.setTimeout(arrived, 1_000);
        }}
      >
        navigate
      </button>
    );
  }

  test("shows under the top bar while a route change is under way, and goes when it arrived", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const { user } = renderWithProviders(
        <AppShell>
          <Screen top="main" title="Markets">
            <Trigger />
          </Screen>
        </AppShell>,
      );
      expect(progress()).not.toBeInTheDocument();

      await user.click(screen.getByRole("button", { name: "navigate" }));

      expect(progress()).toBeInTheDocument();
      expect(topBar()).toContainElement(progress() as HTMLElement);
      // The 300 ms wait and the movement are CSS; with reduced motion the car is not shown.
      expect(progress()!.firstElementChild).toHaveClass("animate-route-progress");
      expect(progress()!.querySelector("svg")).toHaveClass("motion-reduce:hidden");
      expect(progress()).toHaveAttribute("aria-hidden", "true");

      await vi.advanceTimersByTimeAsync(1_100);

      expect(progress()).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  test("a link of the app renders as an ordinary link", () => {
    renderWithProviders(<Link href="/wallet">Wallet</Link>);

    expect(screen.getByRole("link", { name: "Wallet" })).toHaveAttribute("href", "/wallet");
  });
});
