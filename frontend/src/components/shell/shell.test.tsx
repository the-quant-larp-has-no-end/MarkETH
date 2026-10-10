import { useQuery } from "@tanstack/react-query";
import { act, screen, waitFor, within } from "@testing-library/react";
import { usePathname } from "next/navigation";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { meQuery } from "@/lib/api/queries";
import type { UserRole } from "@/lib/api/schema";
import { me } from "@/test/fixtures";
import { deferred, json, mockApi, type Route } from "@/test/http";
import { setViewport } from "@/test/media";
import { navigation } from "@/test/navigation";
import { renderWithProviders } from "@/test/render";

import { AppShell } from "./app-shell";
import { Link, TextLink } from "./link";
import { NotFoundScreen } from "./not-found-screen";
import { Screen } from "./screen";
import { useHideTabBar, useShellStore } from "./shell-store";
import { tabsFor } from "./tabs";
import { BareTopBar } from "./top-bar";

const ME: Route = "GET /api/me";

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
  // On a phone the Leaderboard tab reads "Ranking": a word that fits a fifth of the screen.
  test.each([
    ["USER", ["Markets", "Portfolio", "Ranking", "Wallet"]],
    ["BARISTA", ["Markets", "Portfolio", "Ranking", "Wallet", "Scan"]],
    ["ADMIN", ["Markets", "Portfolio", "Ranking", "Wallet", "Admin"]],
  ] as const)("shows a %s these tabs", (role, tabs) => {
    renderShell({}, { me: me({ role }) });

    expect(tabNames(tabBar()!)).toEqual(tabs);
  });

  test("the Leaderboard tab is named by the word it shows, and leads to the Leaderboard", () => {
    renderShell();

    const tab = within(tabBar()!).getByRole("link", { name: "Ranking" });
    expect(tab).toHaveAttribute("href", "/leaderboard");
    expect(within(tabBar()!).queryByText("Leaderboard")).not.toBeInTheDocument();
  });

  test("every label stands on one line inside its tab, and none is longer than the one that was measured", () => {
    renderShell({}, { me: me({ role: "ADMIN" }) });

    const labels = [...tabBar()!.querySelectorAll("[data-slot=tab-label]")];
    expect(labels).toHaveLength(5);
    for (const label of labels) {
      expect(label).toHaveClass("max-w-full", "truncate");
      expect(label.closest("li")).toHaveClass("min-w-0", "flex-1");
    }
    // Five tabs on a 320 px screen have 64 px each. "Portfolio" is the widest label, and
    // Chrome draws it 48 px wide there ("Leaderboard" takes 74 px): no label of any role
    // has more characters than it.
    for (const role of ["USER", "BARISTA", "ADMIN"] satisfies UserRole[]) {
      for (const tab of tabsFor(role)) {
        expect((tab.short ?? tab.label).length).toBeLessThanOrEqual("Portfolio".length);
      }
    }
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
    ["/leaderboard", "Ranking"],
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
    mockApi({ [ME]: json(200, me()) });
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
    // There the tab has room for its whole name, which is also the title of its screen.
    expect(tabNames(nav)).toEqual(["Markets", "Portfolio", "Leaderboard", "Wallet", "Scan"]);
    expect(within(nav).queryByText("Ranking")).not.toBeInTheDocument();
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
    // Arriving on another screen reads /me again: the API is there to answer.
    beforeEach(() => {
      mockApi({ [ME]: json(200, me()) });
    });
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

  test("before the app knows who is looking it shows the logo and nothing on its right", () => {
    renderWithProviders(<BareTopBar />, { me: null });

    expect(within(topBar()).getByRole("img", { name: "MarkETH" })).toBeInTheDocument();
    expect(within(topBar()).queryByRole("link")).not.toBeInTheDocument();
  });

  test("the bare logo can lead somewhere: to the profile set-up, from the usage policy of a visitor", () => {
    renderWithProviders(<BareTopBar href="/" />, { me: null });

    const link = within(topBar()).getByRole("link", { name: "MarkETH" });
    expect(link).toHaveAttribute("href", "/");
    expect(link).toHaveClass("h-11");
    expect(within(topBar()).getAllByRole("link")).toHaveLength(1);
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

describe("the balance in the top bar", () => {
  const balance = () => within(topBar()).getByRole("link", { name: /^Wallet,/ });

  test("is read again on every arrival on another screen, so it follows payouts, refunds and monthly tokens", async () => {
    // A market the user holds was resolved while they looked at the Markets list.
    const api = mockApi({ [ME]: json(200, me({ balance_cr: "331.64" })) });
    const { user } = renderShell({}, { me: me({ balance_cr: "240" }) });
    expect(balance()).toHaveTextContent("240");
    expect(api.calls(ME)).toHaveLength(0);

    await user.click(within(tabBar()!).getByRole("link", { name: "Portfolio" }));

    expect(navigation.pathname).toBe("/portfolio");
    await waitFor(() => expect(balance()).toHaveTextContent("331.64"));
    expect(api.calls(ME)).toHaveLength(1);

    await user.click(within(tabBar()!).getByRole("link", { name: "Ranking" }));
    await waitFor(() => expect(api.calls(ME)).toHaveLength(2));
  });

  test("the route the page was loaded on reads nothing: start-up has just read /me", async () => {
    const api = mockApi({ [ME]: json(200, me()) });
    navigation.go("/portfolio");

    renderShell();
    await act(() => new Promise((resolve) => setTimeout(resolve, 20)));

    expect(api.calls(ME)).toHaveLength(0);
  });

  test("a change of the query string is no navigation: a sort or an open sheet reads nothing", async () => {
    const api = mockApi({ [ME]: json(200, me()) });
    navigation.go("/leaderboard");
    renderShell();

    act(() => navigation.router.push("/leaderboard?sort=trades"));
    await act(() => new Promise((resolve) => setTimeout(resolve, 20)));

    expect(navigation.href).toBe("/leaderboard?sort=trades");
    expect(api.calls(ME)).toHaveLength(0);
  });

  test("a screen that reads /me itself as it opens shares the one request", async () => {
    const answer = deferred();
    const api = mockApi({ [ME]: answer.handler });
    /** The Wallet reads the balance again whenever it opens. */
    function Wallet() {
      useQuery(meQuery);
      return <p>The wallet</p>;
    }
    function Route() {
      return usePathname() === "/wallet" ? <Wallet /> : <p>Markets</p>;
    }
    const { user } = renderWithProviders(
      <AppShell>
        <Screen top="main" title="MarkETH">
          <Route />
        </Screen>
      </AppShell>,
    );

    await user.click(within(tabBar()!).getByRole("link", { name: "Wallet" }));
    await screen.findByText("The wallet");
    await act(() => new Promise((resolve) => setTimeout(resolve, 20)));

    // One request on the wire, not one that was cut short and a second one.
    expect(api.calls(ME)).toHaveLength(1);
    answer.resolve(json(200, me({ balance_cr: "440" })));
    await waitFor(() => expect(balance()).toHaveTextContent("440"));
    expect(api.calls(ME)).toHaveLength(1);
  });

  test("a /me that cannot be reached leaves the balance that was read before on screen", async () => {
    mockApi({ [ME]: () => Promise.reject(new TypeError("Failed to fetch")) });
    const { user } = renderShell({}, { me: me({ balance_cr: "240" }) });

    await user.click(within(tabBar()!).getByRole("link", { name: "Portfolio" }));
    await act(() => new Promise((resolve) => setTimeout(resolve, 20)));

    expect(balance()).toHaveTextContent("240");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
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

describe("TextLink", () => {
  test("is a link that is text: white, underlined, with a target 44 px tall, and never green or red", () => {
    renderWithProviders(<TextLink href="/policy">Usage policy</TextLink>);

    const link = screen.getByRole("link", { name: "Usage policy" });
    expect(link).toHaveAttribute("href", "/policy");
    expect(link).toHaveClass("text-foreground", "underline", "min-h-11");
    expect(link.className).not.toMatch(/\b(text|bg)-(yes|no)\b/);
    // It stays in the page it is on.
    expect(link).not.toHaveAttribute("target");
  });
});
