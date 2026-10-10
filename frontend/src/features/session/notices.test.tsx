import { QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test } from "vitest";

import { AppShell, Screen } from "@/components/shell";
import { Toaster } from "@/components/ui/sonner";
import { keys } from "@/lib/api/keys";
import type { MeResponse } from "@/lib/api/schema";
import { config, MARKET_ID, me, notice, OTHER_MARKET_ID } from "@/test/fixtures";
import { apiError, deferred, type Handler, json, mockApi, noAnswer, type Route } from "@/test/http";
import { navigation } from "@/test/navigation";
import { createTestQueryClient } from "@/test/render";
import { clearToasts } from "@/test/toasts";

import { AppGate } from "./app-gate";

const CONFIG: Route = "GET /api/config";
const ME: Route = "GET /api/me";
const SEEN: Route = "POST /api/me/notices/{notice_id}/seen";

const APPROVED = notice({
  id: "0e6d4a52-7c1b-4f39-a2d8-5b9c3e1f7a60",
  market_id: MARKET_ID,
  question: "Will the Polybahn run without interruption during the exam session?",
});
const SECOND = notice({
  id: "5c1f0b7e-93a4-4d28-b6e1-2a7d9c4f8e13",
  market_id: OTHER_MARKET_ID,
  question: "Will the ASVZ open a second climbing wall before the end of the semester?",
  created_at: "2026-10-06T09:00:00Z",
});

const SENTENCE = `Your market is open: ${APPROVED.question}`;

/** The app as it stands in a browser: start-up, a route in the shell, and the toasts. */
function renderApp(client = createTestQueryClient()) {
  const view = render(
    <QueryClientProvider client={client}>
      <AppGate>
        <AppShell>
          <Screen top="main" title="Markets">
            <p>The markets</p>
          </Screen>
        </AppShell>
      </AppGate>
      <Toaster />
    </QueryClientProvider>,
  );
  return { ...view, client, user: userEvent.setup() };
}

/** `/me` for a profile with these notices, and the API marking any of them seen. */
function api(notices: MeResponse["notices"], seen: Handler = json(200, APPROVED)) {
  return mockApi({
    [CONFIG]: json(200, config()),
    [ME]: json(200, me({ notices })),
    [SEEN]: seen,
  });
}

const toasts = () => Array.from(document.querySelectorAll<HTMLElement>("[data-sonner-toast]"));

describe("a notice of /me", () => {
  test("is shown as a toast with its sentence and the link 'View', which leads to the market", async () => {
    api([APPROVED]);
    renderApp();

    const text = await screen.findByText(SENTENCE);
    const toast = text.closest<HTMLElement>("[data-sonner-toast]")!;
    expect(toast).not.toBeNull();
    const view = within(toast).getByRole("link", { name: "View" });
    expect(view).toHaveAttribute("href", `/markets/${MARKET_ID}`);
    // A text link with a target 44 px tall, as every link that is text.
    expect(view).toHaveAttribute("data-slot", "text-link");
    expect(view).toHaveClass("min-h-11", "underline");
    // Like every toast it has its close cross.
    expect(within(toast).getByRole("button", { name: "Close" })).toBeInTheDocument();
    // The route that was opened is there behind it: a toast stands in front of nothing.
    expect(screen.getByText("The markets")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  test("is then marked seen: its id in the address, nothing in the body", async () => {
    const calls = api([APPROVED]);
    renderApp();
    await screen.findByText(SENTENCE);

    await waitFor(() => expect(calls.calls(SEEN)).toHaveLength(1));
    const sent = calls.lastCall(SEEN)!;
    expect(sent.path).toBe(`/api/me/notices/${APPROVED.id}/seen`);
    expect(sent.rawBody).toBe("");
    expect(sent.query.toString()).toBe("");
  });

  test("leaves the notices of /me in the cache once the API has marked it seen", async () => {
    api([APPROVED]);
    const { client } = renderApp();
    await screen.findByText(SENTENCE);

    await waitFor(() => expect(client.getQueryData<MeResponse>(keys.me)?.notices).toEqual([]));
    // The rest of the profile is as it was.
    expect(client.getQueryData<MeResponse>(keys.me)?.balance_cr).toBe("240");
  });

  test("'View' opens the market and puts the toast away", async () => {
    api([APPROVED]);
    const { user } = renderApp();
    const text = await screen.findByText(SENTENCE);

    await user.click(within(text.closest<HTMLElement>("[data-sonner-toast]")!).getByRole("link"));

    expect(navigation.pathname).toBe(`/markets/${MARKET_ID}`);
    await waitFor(() => expect(screen.queryByText(SENTENCE)).not.toBeInTheDocument());
  });

  test("the cross puts the toast away, and the notice stays told", async () => {
    const calls = api([APPROVED]);
    const { user } = renderApp();
    const text = await screen.findByText(SENTENCE);

    await user.click(
      within(text.closest<HTMLElement>("[data-sonner-toast]")!).getByRole("button", {
        name: "Close",
      }),
    );

    await waitFor(() => expect(screen.queryByText(SENTENCE)).not.toBeInTheDocument());
    expect(navigation.pathname).toBe("/");
    await waitFor(() => expect(calls.calls(SEEN)).toHaveLength(1));
  });

  test("several notices are each told once, oldest first, and each is marked seen", async () => {
    const calls = api([APPROVED, SECOND], json(200, APPROVED));
    renderApp();

    await screen.findByText(SENTENCE);
    await screen.findByText(`Your market is open: ${SECOND.question}`);
    expect(toasts()).toHaveLength(2);
    expect(
      screen
        .getAllByRole("link", { name: "View" })
        .map((link) => link.getAttribute("href"))
        .sort(),
    ).toEqual([`/markets/${MARKET_ID}`, `/markets/${OTHER_MARKET_ID}`].sort());

    await waitFor(() => expect(calls.calls(SEEN)).toHaveLength(2));
    // In the order /me lists them, which is oldest first.
    expect(calls.calls(SEEN).map((request) => request.path)).toEqual([
      `/api/me/notices/${APPROVED.id}/seen`,
      `/api/me/notices/${SECOND.id}/seen`,
    ]);
  });

  test("with nothing to tell there is no toast and no request", async () => {
    const calls = api([]);
    renderApp();
    await screen.findByText("The markets");

    expect(toasts()).toHaveLength(0);
    expect(calls.calls(SEEN)).toHaveLength(0);
  });

  test("one that arrives with a later /me is told then", async () => {
    const calls = api([]);
    const { client } = renderApp();
    await screen.findByText("The markets");
    expect(toasts()).toHaveLength(0);

    // VIS approves the proposal; the next /me, of a navigation or of the focus, lists it.
    calls.on(ME, json(200, me({ notices: [APPROVED] })));
    await act(() => client.invalidateQueries({ queryKey: keys.me, exact: true }));

    expect(await screen.findByText(SENTENCE)).toBeInTheDocument();
    await waitFor(() => expect(calls.calls(SEEN)).toHaveLength(1));
  });
});

describe("a notice that was shown is not shown a second time by this page", () => {
  test.each<[string, Handler]>([
    ["no response", noAnswer],
    ["an error of the server", apiError(500, "INTERNAL_ERROR", "Something went wrong.")],
    ["a notice the API does not find", apiError(404, "NOT_FOUND", "This does not exist.")],
  ])(
    "marking it seen failed (%s): /me lists it again, and it is neither told nor sent again",
    async (_name, failure) => {
      const calls = api([APPROVED], failure);
      const { client, user } = renderApp();
      const text = await screen.findByText(SENTENCE);
      await waitFor(() => expect(calls.calls(SEEN)).toHaveLength(1));

      // The failure of this write is nobody's to read: no banner, no second toast.
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      expect(toasts()).toHaveLength(1);
      await user.click(
        within(text.closest<HTMLElement>("[data-sonner-toast]")!).getByRole("button", {
          name: "Close",
        }),
      );
      await waitFor(() => expect(toasts()).toHaveLength(0));

      // The notice is still unseen on the server: every later /me lists it.
      for (let read = 0; read < 3; read += 1) {
        calls.on(ME, json(200, me({ notices: [APPROVED], balance_cr: `24${read}` })));
        await act(() => client.invalidateQueries({ queryKey: keys.me, exact: true }));
        await waitFor(() =>
          expect(client.getQueryData<MeResponse>(keys.me)?.balance_cr).toBe(`24${read}`),
        );
      }

      expect(toasts()).toHaveLength(0);
      expect(screen.queryByText(SENTENCE)).not.toBeInTheDocument();
      // A write is never sent again by itself.
      expect(calls.calls(SEEN)).toHaveLength(1);
    },
  );

  test("a /me that was asked before the notice was marked, and answers after: no second toast", async () => {
    const late = deferred();
    const calls = api([APPROVED]);
    const { client, user } = renderApp();
    const text = await screen.findByText(SENTENCE);
    await waitFor(() => expect(client.getQueryData<MeResponse>(keys.me)?.notices).toEqual([]));
    await user.click(
      within(text.closest<HTMLElement>("[data-sonner-toast]")!).getByRole("button", {
        name: "Close",
      }),
    );
    await waitFor(() => expect(toasts()).toHaveLength(0));

    // An answer that was put together before the mark arrived still lists the notice.
    calls.on(ME, late.handler);
    const read = client.invalidateQueries({ queryKey: keys.me, exact: true });
    late.resolve(json(200, me({ notices: [APPROVED] })));
    await act(() => read);

    await waitFor(() => expect(client.getQueryData<MeResponse>(keys.me)?.notices).toHaveLength(1));
    expect(toasts()).toHaveLength(0);
    expect(calls.calls(SEEN)).toHaveLength(1);
  });

  test("the app is taken down and put up again in the same page: still told once", async () => {
    const calls = api([APPROVED], noAnswer);
    const client = createTestQueryClient();
    const first = renderApp(client);
    const text = await screen.findByText(SENTENCE);
    await waitFor(() => expect(calls.calls(SEEN)).toHaveLength(1));
    await first.user.click(
      within(text.closest<HTMLElement>("[data-sonner-toast]")!).getByRole("button", {
        name: "Close",
      }),
    );
    await waitFor(() => expect(toasts()).toHaveLength(0));
    first.unmount();

    renderApp(client);
    await screen.findByText("The markets");
    await waitFor(() => expect(calls.calls(ME).length).toBeGreaterThanOrEqual(2));

    expect(screen.queryByText(SENTENCE)).not.toBeInTheDocument();
    expect(calls.calls(SEEN)).toHaveLength(1);
  });

  test("another page load tells a notice that is still unseen once more", async () => {
    // Two query clients are two page loads: what one told, the other does not know.
    const calls = api([APPROVED], noAnswer);
    const first = renderApp();
    const text = await screen.findByText(SENTENCE);
    await waitFor(() => expect(calls.calls(SEEN)).toHaveLength(1));
    await first.user.click(
      within(text.closest<HTMLElement>("[data-sonner-toast]")!).getByRole("button", {
        name: "Close",
      }),
    );
    await waitFor(() => expect(toasts()).toHaveLength(0));
    first.unmount();
    // The page that loads next knows nothing of the toasts of this one either.
    await clearToasts();

    renderApp();

    expect(await screen.findByText(SENTENCE)).toBeInTheDocument();
    await waitFor(() => expect(calls.calls(SEEN)).toHaveLength(2));
  });
});

describe("who is told", () => {
  test("a visitor without a profile is told nothing, and nothing is asked for them", async () => {
    const calls = mockApi({
      [CONFIG]: json(200, config()),
      [ME]: apiError(403, "PROFILE_REQUIRED", "Set up your profile to continue.", {
        name: "Anna Muster",
        email: "anna.muster@ethz.ch",
      }),
    });
    renderApp();

    await screen.findByRole("button", { name: "Create profile" });
    expect(toasts()).toHaveLength(0);
    expect(calls.calls(SEEN)).toHaveLength(0);
  });

  test("a kind of notice this app has no sentence for is neither told nor marked seen", async () => {
    const unknown = { ...APPROVED, kind: "SOMETHING_ELSE" } as unknown as MeResponse["notices"][0];
    const calls = api([unknown, SECOND]);
    renderApp();

    // The one it knows is told, in its own words.
    await screen.findByText(`Your market is open: ${SECOND.question}`);
    await waitFor(() => expect(calls.calls(SEEN)).toHaveLength(1));
    expect(toasts()).toHaveLength(1);
    expect(calls.lastCall(SEEN)?.path).toBe(`/api/me/notices/${SECOND.id}/seen`);
    expect(document.body).not.toHaveTextContent("SOMETHING_ELSE");
  });
});
