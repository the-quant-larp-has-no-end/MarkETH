import { QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, test } from "vitest";

import { keys } from "@/lib/api/keys";
import { liveTopic, useLive, useLiveTopic } from "@/lib/live";
import { installFakeEventSource } from "@/test/fake-event-source";
import { config, MARKET_ID, me, readyEvent } from "@/test/fixtures";
import { apiError, deferred, json, mockApi, type Route } from "@/test/http";
import { createTestQueryClient } from "@/test/render";

import { AppGate } from "./app-gate";

const CONFIG: Route = "GET /api/config";
const ME: Route = "GET /api/me";

const PROFILE_REQUIRED = apiError(403, "PROFILE_REQUIRED", "Set up your profile to continue.", {
  name: "Anna Muster",
  email: "anna.muster@ethz.ch",
});

/** The route that was opened: a screen that says whether the stream is open. */
function Wallet() {
  const { live } = useLive();
  return <p>The wallet, {live ? "live" : "not live"}</p>;
}

/** A market's page in miniature: it names the live stream of its market. */
function Market() {
  useLiveTopic(liveTopic.broadcast(MARKET_ID));
  return <p>The market</p>;
}

function renderApp(children: ReactNode = <Wallet />) {
  const client = createTestQueryClient();
  const view = render(
    <QueryClientProvider client={client}>
      <AppGate>{children}</AppGate>
    </QueryClientProvider>,
  );
  return { ...view, client };
}

describe("the event stream", () => {
  let streams: ReturnType<typeof installFakeEventSource>;

  beforeEach(() => {
    // The test plays the stream: one that is opened and says nothing asks the API for nothing.
    streams = installFakeEventSource();
  });

  test("is held by whoever has a profile: one stream, named by the tab", async () => {
    mockApi({ [CONFIG]: json(200, config()), [ME]: json(200, me()) });

    renderApp();
    await screen.findByText("The wallet, not live");

    expect(streams.instances).toHaveLength(1);
    expect(streams.held()).toHaveLength(1);
    expect(streams.latest().tab).toMatch(/^[A-Za-z0-9_-]{8,36}$/);
    expect(new URL(streams.latest().url, window.location.origin).pathname).toBe("/api/stream");
  });

  test("is what the route's components follow: the stream names their topics and says when it is open", async () => {
    mockApi({ [CONFIG]: json(200, config()), [ME]: json(200, me()) });

    renderApp(
      <>
        <Wallet />
        <Market />
      </>,
    );
    await screen.findByText("The market");

    expect(streams.instances).toHaveLength(1);
    expect(streams.latest().topics).toEqual([`broadcast:${MARKET_ID}`]);

    act(() => streams.latest().send(readyEvent()));

    expect(screen.getByText("The wallet, live")).toBeInTheDocument();
  });

  test("is not opened before the app knows who is looking", async () => {
    const answer = deferred();
    mockApi({ [CONFIG]: json(200, config()), [ME]: answer.handler });

    renderApp();
    await screen.findByRole("status");

    expect(streams.instances).toHaveLength(0);
    answer.resolve(json(200, me()));
    await screen.findByText("The wallet, not live");
    expect(streams.instances).toHaveLength(1);
  });

  test("is not opened for a caller without a profile, and is once /me answers the profile", async () => {
    const api = mockApi({ [CONFIG]: json(200, config()), [ME]: PROFILE_REQUIRED });
    const { client } = renderApp();

    await screen.findByLabelText("Username");
    expect(streams.instances).toHaveLength(0);

    api.on(ME, json(200, me()));
    await act(() => client.invalidateQueries({ queryKey: keys.me, exact: true }));
    await screen.findByText("The wallet, not live");

    expect(streams.held()).toHaveLength(1);
  });

  test("is not opened for a caller the API refuses", async () => {
    mockApi({
      [CONFIG]: json(200, config()),
      [ME]: apiError(403, "FORBIDDEN", "This account is suspended."),
    });

    renderApp();
    await screen.findByRole("alert");

    expect(streams.instances).toHaveLength(0);
  });

  test("is let go when /me refuses the caller while the app is open", async () => {
    const api = mockApi({ [CONFIG]: json(200, config()), [ME]: json(200, me()) });
    const { client } = renderApp();
    await screen.findByText("The wallet, not live");
    act(() => streams.latest().send(readyEvent()));

    api.on(ME, apiError(403, "FORBIDDEN", "This account is suspended."));
    await act(() => client.invalidateQueries({ queryKey: keys.me, exact: true }));

    expect(await screen.findByRole("alert")).toHaveTextContent("This account is suspended.");
    expect(streams.held()).toHaveLength(0);
  });

  test("stays the one it is when /me is read again, and asks the API for nothing when it opens", async () => {
    const api = mockApi({ [CONFIG]: json(200, config()), [ME]: json(200, me()) });
    const { client } = renderApp();
    await screen.findByText("The wallet, not live");
    const asked = api.requests.length;

    act(() => streams.latest().send(readyEvent()));
    await screen.findByText("The wallet, live");
    // `/me` is read as the app reads it without the stream: a stream that opens reads none.
    await act(() => new Promise((resolve) => setTimeout(resolve, 120)));
    expect(api.requests).toHaveLength(asked);

    api.on(ME, json(200, me({ balance_cr: "331.64" })));
    await act(() => client.invalidateQueries({ queryKey: keys.me, exact: true }));
    await waitFor(() => expect(api.calls(ME)).toHaveLength(2));

    expect(streams.instances).toHaveLength(1);
    expect(streams.held()).toHaveLength(1);
    expect(screen.getByText("The wallet, live")).toBeInTheDocument();
  });
});
