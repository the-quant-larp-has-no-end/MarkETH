import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, test, vi } from "vitest";

import { AppShell, Screen } from "@/components/shell";
import { keys } from "@/lib/api/keys";
import { page } from "@/lib/browser";
import { config, me, profileSaved } from "@/test/fixtures";
import {
  apiError,
  deferred,
  errorPage,
  json,
  mockApi,
  noAnswer,
  portal401,
  type Route,
} from "@/test/http";
import { navigation } from "@/test/navigation";
import { createTestQueryClient } from "@/test/render";

import { AppGate, useWelcomeOpen } from "./app-gate";

const CONFIG: Route = "GET /api/config";
const ME: Route = "GET /api/me";
const SAVE: Route = "PUT /api/me/profile";

const PROFILE_REQUIRED = apiError(403, "PROFILE_REQUIRED", "Set up your profile to continue.", {
  name: "Anna Muster",
  email: "anna.muster@ethz.ch",
});

/** The route that was opened: a screen in the shell that says whether the welcome dialog is up. */
function Route() {
  const welcome = useWelcomeOpen();
  return (
    <AppShell>
      <Screen top="main" title="Wallet">
        <p>The wallet{welcome ? ", waiting for the welcome dialog" : ""}</p>
      </Screen>
    </AppShell>
  );
}

function renderApp(children: ReactNode = <Route />) {
  const client = createTestQueryClient();
  const view = render(
    <QueryClientProvider client={client}>
      <AppGate>{children}</AppGate>
    </QueryClientProvider>,
  );
  return { ...view, client, user: userEvent.setup() };
}

beforeEach(() => {
  vi.spyOn(page, "reload").mockImplementation(() => {});
});

describe("start-up", () => {
  test("shows the boot screen until /config and /me have answered: the logo, no balance, no tabs", async () => {
    const answer = deferred();
    mockApi({ [CONFIG]: json(200, config()), [ME]: answer.handler });

    renderApp();

    expect(screen.getByRole("img", { name: "MarkETH" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Loading");
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Profile" })).not.toBeInTheDocument();
    expect(screen.queryByText("The wallet")).not.toBeInTheDocument();

    answer.resolve(json(200, me()));
    expect(await screen.findByText("The wallet")).toBeInTheDocument();
  });

  test("asks /config and /me, and nothing else", async () => {
    const api = mockApi({ [CONFIG]: json(200, config()), [ME]: json(200, me()) });

    renderApp();
    await screen.findByText("The wallet");

    expect(api.requests.map((request) => `${request.method} ${request.path}`).sort()).toEqual([
      "GET /api/config",
      "GET /api/me",
    ]);
  });

  test("with a profile, shows the route that was opened inside the shell", async () => {
    mockApi({
      [CONFIG]: json(200, config()),
      [ME]: json(200, me({ balance_cr: "1250.5" })),
    });

    renderApp();

    expect(await screen.findByText("The wallet")).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Main" })).toBeInTheDocument();
    // The balance of /me is in the top bar, as the string the API sent.
    expect(screen.getByRole("link", { name: /Wallet, 1,250\.5 tokens/ })).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("a caller without a profile", () => {
  test("sees the set-up screen in place of the route, with the portal's name and no tab bar", async () => {
    mockApi({ [CONFIG]: json(200, config()), [ME]: PROFILE_REQUIRED });
    navigation.go("/wallet?code=4F7K-Q2M9-XAB3");

    renderApp();

    expect(await screen.findByRole("button", { name: "Create profile" })).toBeInTheDocument();
    expect(screen.getByText("Signed in as Anna Muster")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Trade on campus questions with free tokens. Enough of them get you a coffee at the bar.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Pick a name for the leaderboard. The podium remembers."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
    expect(screen.queryByText("The wallet")).not.toBeInTheDocument();
    // The address stays as it was opened, so the code card's code survives the set-up.
    expect(navigation.href).toBe("/wallet?code=4F7K-Q2M9-XAB3");
  });

  test("is named by the address when the portal sent no name", async () => {
    mockApi({
      [CONFIG]: json(200, config()),
      [ME]: apiError(403, "PROFILE_REQUIRED", "Set up your profile to continue.", {
        name: "",
        email: "anna.muster@ethz.ch",
      }),
    });

    renderApp();

    expect(await screen.findByText("Signed in as anna.muster@ethz.ch")).toBeInTheDocument();
  });

  test("continues to the route once the profile is saved, and is welcomed with the answer's balance", async () => {
    const api = mockApi({
      [CONFIG]: json(200, config({ signup_grant_cr: "750" })),
      [ME]: PROFILE_REQUIRED,
      [SAVE]: json(
        200,
        profileSaved({ username: "anna_m", balance_cr: "500", signup_granted: true }),
      ),
    });
    const { user, client } = renderApp();

    await user.type(await screen.findByLabelText("Username"), "anna_m");
    await user.selectOptions(screen.getByLabelText("Level"), "Bachelor");
    await user.selectOptions(screen.getByLabelText("Programme"), "BSc Computer Science");
    await user.click(screen.getByRole("button", { name: "Create profile" }));

    // The route that was opened takes the place of the set-up screen.
    expect(await screen.findByText(/The wallet/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Create profile" })).not.toBeInTheDocument();
    expect(api.lastCall(SAVE)?.body).toEqual({
      username: "anna_m",
      degree_level: "BACHELOR",
      degree_programme: "BSc Computer Science",
    });

    // The welcome dialog opens over it. Its amount is the answer's balance, not a tunable.
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("heading", { name: "You got 500 tokens" })).toBeInTheDocument();
    expect(
      within(dialog).getByText(
        "Spend them wisely, or on coffee. A share pays 1 token if you are right and nothing if you are wrong.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("The wallet, waiting for the welcome dialog")).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Start trading" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.getByText("The wallet")).toBeInTheDocument();
    // The saved profile is the current user; /me was not asked a second time for it.
    expect(client.getQueryData(keys.me)).toMatchObject({ username: "anna_m", balance_cr: "500" });
    expect(api.calls(ME)).toHaveLength(1);
  });

  test("is not welcomed again when the save did not create the profile", async () => {
    mockApi({
      [CONFIG]: json(200, config()),
      [ME]: PROFILE_REQUIRED,
      [SAVE]: json(200, profileSaved({ signup_granted: false })),
    });
    const { user } = renderApp();

    await user.type(await screen.findByLabelText("Username"), "anna_m");
    await user.selectOptions(screen.getByLabelText("Level"), "Bachelor");
    await user.selectOptions(screen.getByLabelText("Programme"), "BSc Computer Science");
    await user.click(screen.getByRole("button", { name: "Create profile" }));

    expect(await screen.findByText("The wallet")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("a caller the API refuses", () => {
  test("FORBIDDEN shows the API's message full screen, with nothing to try again", async () => {
    mockApi({
      [CONFIG]: json(200, config()),
      [ME]: apiError(403, "FORBIDDEN", "MarkETH is open to ETH addresses only.", {
        allowed_domains: ["ethz.ch"],
      }),
    });

    renderApp();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "MarkETH is open to ETH addresses only.",
    );
    expect(screen.getByRole("img", { name: "MarkETH" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
    expect(screen.queryByText("The wallet")).not.toBeInTheDocument();
  });

  test("UNAUTHENTICATED shows the API's message with Try again, which reloads the page", async () => {
    mockApi({
      [CONFIG]: json(200, config()),
      [ME]: apiError(401, "UNAUTHENTICATED", "Sign in through the ETH portal to continue."),
    });
    const { user } = renderApp();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Sign in through the ETH portal to continue.",
    );
    expect(page.reload).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Try again" }));

    expect(page.reload).toHaveBeenCalledTimes(1);
  });

  test("a suspension that arrives while the app is open replaces it with the message", async () => {
    const api = mockApi({ [CONFIG]: json(200, config()), [ME]: json(200, me()) });
    const { client } = renderApp();
    await screen.findByText("The wallet");

    api.on(ME, apiError(403, "FORBIDDEN", "This account is suspended."));
    await client.invalidateQueries({ queryKey: keys.me, exact: true });

    expect(await screen.findByRole("alert")).toHaveTextContent("This account is suspended.");
    expect(screen.queryByText("The wallet")).not.toBeInTheDocument();
  });
});

describe("the portal's 401", () => {
  test("reloads the page once and shows nothing but the boot screen meanwhile", async () => {
    mockApi({ [CONFIG]: portal401(), [ME]: portal401() });

    renderApp();

    await waitFor(() => expect(page.reload).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("status")).toHaveTextContent("Loading");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  test("says that the sign-in has ended when the reload brought the same answer", async () => {
    // The mark of the reload that already happened.
    window.sessionStorage.setItem("marketh.reloaded", "1");
    mockApi({ [CONFIG]: portal401(), [ME]: portal401() });
    const { user } = renderApp();

    expect(await screen.findByRole("alert")).toHaveTextContent("Your ETH sign-in has ended.");
    expect(page.reload).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(page.reload).toHaveBeenCalledTimes(1);
  });

  test("may reload again later, once /me has answered in between", async () => {
    window.sessionStorage.setItem("marketh.reloaded", "1");
    mockApi({ [CONFIG]: json(200, config()), [ME]: json(200, me()) });

    renderApp();
    await screen.findByText("The wallet");

    await waitFor(() => expect(window.sessionStorage.getItem("marketh.reloaded")).toBeNull());
  });
});

describe("no answer", () => {
  test("a /me that cannot be reached shows the connection message with Try again", async () => {
    mockApi({ [CONFIG]: json(200, config()), [ME]: noAnswer });

    renderApp();

    expect(await screen.findByRole("alert")).toHaveTextContent("No connection. Try again.");
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
    expect(screen.queryByText("The wallet")).not.toBeInTheDocument();
  });

  test("an error page in place of the API counts as temporarily unavailable", async () => {
    mockApi({ [CONFIG]: errorPage(404), [ME]: errorPage(404) });

    renderApp();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This is temporarily unavailable. Try again shortly.",
    );
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  test("a /config that fails counts the same, even when /me answered", async () => {
    mockApi({
      [CONFIG]: apiError(
        503,
        "SERVICE_UNAVAILABLE",
        "This is temporarily unavailable. Try again shortly.",
      ),
      [ME]: json(200, me()),
    });

    renderApp();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This is temporarily unavailable. Try again shortly.",
    );
    expect(screen.queryByText("The wallet")).not.toBeInTheDocument();
  });

  test("a profile that was loaded stays on screen when a later /me cannot be reached", async () => {
    const api = mockApi({ [CONFIG]: json(200, config()), [ME]: json(200, me()) });
    const { client } = renderApp();
    await screen.findByText("The wallet");

    api.on(ME, noAnswer);
    await client.invalidateQueries({ queryKey: keys.me, exact: true });

    expect(screen.getByText("The wallet")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
