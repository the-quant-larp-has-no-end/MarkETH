import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { keys } from "@/lib/api/keys";
import type {
  BroadcastItem,
  BroadcastStatus,
  BroadcastViewer,
  MarketBroadcastResponse,
} from "@/lib/api/schema";
import { installFakeEventSource } from "@/test/fake-event-source";
import {
  adminMarketDetail,
  BROADCAST_ID,
  broadcastChangedEvent,
  broadcastIn,
  broadcastPerson,
  broadcastViewer,
  config,
  MARKET_ID,
  marketBroadcast,
  me,
  noBroadcast,
  readyEvent,
} from "@/test/fixtures";
import { apiError, deferred, type Handler, json, mockApi, noAnswer, type Route } from "@/test/http";
import { createTestQueryClient, renderWithProviders } from "@/test/render";

import { BroadcastPanel } from "./broadcast-panel";

const BROADCAST: Route = "GET /api/markets/{market_id}/broadcast";
const APPROVE: Route = "POST /api/admin/broadcasts/{broadcast_id}/approve";
const REFUSE: Route = "POST /api/admin/broadcasts/{broadcast_id}/refuse";
const END: Route = "POST /api/broadcasts/{broadcast_id}/end";
const ME: Route = "GET /api/me";

const DECIDED = "This request has been decided already.";

const adminUser = me({ role: "ADMIN" });

/** A live stream as an admin is told of it: with who asked for it. */
const stream = (status: BroadcastStatus, overrides: Partial<BroadcastItem> = {}) =>
  broadcastIn(status, { requested_by: broadcastPerson(), ...overrides });

/** What an admin may do, and nothing to watch unless the stream is on. */
const may = (flags: Partial<BroadcastViewer> = {}) =>
  broadcastViewer({ can_watch: false, ...flags });

const nothing = () => noBroadcast({ can_start_as_admin: true });
const requested = () =>
  marketBroadcast({
    broadcast: stream("REQUESTED"),
    viewer: may({ can_approve: true, can_refuse: true }),
  });
const approved = () =>
  marketBroadcast({ broadcast: stream("APPROVED"), viewer: may({ can_refuse: true }) });
const refused = () =>
  marketBroadcast({ broadcast: stream("REFUSED"), viewer: may({ can_start_as_admin: true }) });
const live = () =>
  marketBroadcast({ broadcast: stream("LIVE"), viewer: may({ can_watch: true, can_end: true }) });
const ended = () =>
  marketBroadcast({
    broadcast: stream("ENDED", { end_reason: "ADMIN" }),
    viewer: may({ can_start_as_admin: true }),
  });

function show(
  answer: MarketBroadcastResponse | Handler,
  routes: Partial<Record<Route, Handler>> = {},
  options: Parameters<typeof renderWithProviders>[1] = {},
) {
  const read = answer instanceof Response || typeof answer === "function";
  const api = mockApi({
    [BROADCAST]: read ? answer : json(200, answer),
    [ME]: json(200, adminUser),
    ...routes,
  });
  const rendered = renderWithProviders(<BroadcastPanel marketId={MARKET_ID} />, {
    me: adminUser,
    ...options,
  });
  return { api, ...rendered };
}

const block = () => screen.findByRole("region", { name: "Live stream" });
const heading = () => screen.getByRole("heading", { level: 2, name: "Live stream" });
/**
 * What the block says about the live stream, once its read has answered: the one line that
 * is announced when it changes.
 */
async function line(): Promise<HTMLElement> {
  const region = await block();
  return waitFor(() => {
    const sentence = region.querySelector<HTMLElement>("p[role=status]");
    if (sentence === null) throw new Error("The block shows no sentence yet.");
    return sentence;
  });
}
/** The buttons and the links of the block, by their names, in their order. */
const offered = (within_: HTMLElement) =>
  [...within_.querySelectorAll("a, button")].map((control) => control.textContent);

const gone = () => waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());

describe("what the block shows", () => {
  test("no live stream: the sentence, and a link to the studio while the server offers a start", async () => {
    show(nothing());

    expect(await line()).toHaveTextContent("No live stream.");
    const start = within(await block()).getByRole("link", { name: "Start a live stream" });
    expect(start).toHaveAttribute("href", `/markets/${MARKET_ID}/live`);
    expect(start).toHaveAttribute("data-variant", "secondary");
    expect(offered(await block())).toEqual(["Start a live stream"]);
    expect(screen.getAllByRole("heading", { level: 2 })).toHaveLength(1);
  });

  test("a request that was refused leaves no live stream, and the same link", async () => {
    show(refused());

    expect(await line()).toHaveTextContent("No live stream.");
    expect(offered(await block())).toEqual(["Start a live stream"]);
    // The reason of the refusal is the requester's to read, on the market's page.
    expect(await block()).not.toHaveTextContent("The market closes before the event starts.");
  });

  test("a stream that has ended: when it ended, as a date and a time of /config's timezone", async () => {
    show(ended());

    expect(await line()).toHaveTextContent("The last live stream ended on 11 Oct 2026, 11:58.");
    expect(offered(await block())).toEqual(["Start a live stream"]);
  });

  test("a request: who asked and when, with Approve and a destructive Refuse", async () => {
    show(requested());

    expect(await line()).toHaveTextContent(
      "Requested by cab_coffee (bea.beispiel@ethz.ch) on 11 Oct 2026, 10:30.",
    );
    const region = await block();
    expect(offered(region)).toEqual(["Approve", "Refuse"]);
    expect(within(region).getByRole("button", { name: "Approve" })).toHaveAttribute(
      "data-variant",
      "secondary",
    );
    expect(within(region).getByRole("button", { name: "Refuse" })).toHaveAttribute(
      "data-variant",
      "destructive",
    );
  });

  test("an approved request: who can go live, and Refuse alone", async () => {
    show(approved());

    expect(await line()).toHaveTextContent("Approved. cab_coffee can go live.");
    expect(offered(await block())).toEqual(["Refuse"]);
  });

  test("a stream that is on: the Live badge, the time of day of its start, Watch and End stream, and no count of viewers", async () => {
    show(live());

    expect(await line()).toHaveTextContent("Live since 11:45.");
    const region = await block();
    expect(within(region).getByText("Live")).toHaveAttribute("data-slot", "badge");
    expect(within(region).getByRole("link", { name: "Watch" })).toHaveAttribute(
      "href",
      `/markets/${MARKET_ID}`,
    );
    expect(within(region).getByRole("button", { name: "End stream" })).toHaveAttribute(
      "data-variant",
      "destructive",
    );
    expect(offered(region)).toEqual(["Watch", "End stream"]);
    expect(region).not.toHaveTextContent(/watching|viewers/i);
  });

  test.each<[string, MarketBroadcastResponse, (string | null)[]]>([
    ["no start", noBroadcast(), []],
    ["no decision", marketBroadcast({ broadcast: stream("REQUESTED"), viewer: may() }), []],
    [
      "a refusal without an approval",
      marketBroadcast({ broadcast: stream("REQUESTED"), viewer: may({ can_refuse: true }) }),
      ["Refuse"],
    ],
    [
      "no end",
      marketBroadcast({ broadcast: stream("LIVE"), viewer: may({ can_watch: true }) }),
      ["Watch"],
    ],
  ])("a button is there by its flag alone: %s", async (_name, answer, controls) => {
    show(answer);

    await line();
    expect(offered(await block())).toEqual(controls);
  });

  test("a status and an end reason arrive as keys and are never shown", async () => {
    show(ended());

    const region = await block();
    await line();
    expect(region).not.toHaveTextContent(/ENDED|ADMIN|REFUSED|REQUESTED/);
  });

  test("where the API names nobody, the sentences do without", async () => {
    const { api, client } = show(
      marketBroadcast({
        broadcast: stream("REQUESTED", { requested_by: null }),
        viewer: may(),
      }),
    );
    expect(await line()).toHaveTextContent("Requested on 11 Oct 2026, 10:30.");

    api.on(
      BROADCAST,
      json(
        200,
        marketBroadcast({ broadcast: stream("APPROVED", { requested_by: null }), viewer: may() }),
      ),
    );
    await act(() => client.invalidateQueries({ queryKey: keys.markets.broadcast(MARKET_ID) }));

    await waitFor(async () => expect(await line()).toHaveTextContent(/^Approved\.$/));
  });
});

describe("the states of the read", () => {
  test("absent while the read answers NOT_FOUND, as it does for a proposal: no heading, no sentence, no banner", async () => {
    const { api } = show(apiError(404, "NOT_FOUND", "Not found."));

    await waitFor(() =>
      expect(document.querySelector("[data-slot=broadcast-panel]")).not.toBeInTheDocument(),
    );
    expect(api.calls(BROADCAST)).toHaveLength(1);
    expect(screen.queryByRole("heading")).not.toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  test("while the read is on its way: the heading and a skeleton", async () => {
    const answer = deferred();
    show(answer.handler);

    expect(heading()).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Loading");
    expect(screen.queryByRole("button")).not.toBeInTheDocument();

    answer.resolve(json(200, nothing()));

    expect(await line()).toHaveTextContent("No live stream.");
  });

  test("on a market that was opened the skeleton holds the place of the block", async () => {
    const client = createTestQueryClient();
    client.setQueryData(keys.markets.detail(MARKET_ID), adminMarketDetail());
    const answer = deferred();
    show(answer.handler, {}, { client });

    expect(heading()).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Loading");

    answer.resolve(json(200, nothing()));
    expect(await line()).toHaveTextContent("No live stream.");
  });

  test("on a market the cache shows as a proposal no place is held while the read is on its way; the read is made, and its answer decides", async () => {
    const client = createTestQueryClient();
    client.setQueryData(
      keys.markets.detail(MARKET_ID),
      adminMarketDetail({ status: "PROPOSED", trading_open: false }),
    );
    const answer = deferred();
    const { api } = show(answer.handler, {}, { client });

    await waitFor(() => expect(api.calls(BROADCAST)).toHaveLength(1));
    expect(document.querySelector("[data-slot=broadcast-panel]")).not.toBeInTheDocument();
    expect(screen.queryByRole("heading")).not.toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();

    // The cache was behind: the market was opened in the meantime, and the API says so.
    answer.resolve(json(200, nothing()));

    expect(await line()).toHaveTextContent("No live stream.");
    expect(offered(await block())).toEqual(["Start a live stream"]);
  });

  test("a read that failed shows the API's message with Try again, which reads again", async () => {
    const { api, user } = show(
      apiError(500, "INTERNAL_ERROR", "Something went wrong on our side."),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong on our side.");
    expect(heading()).toBeInTheDocument();

    api.on(BROADCAST, json(200, requested()));
    await user.click(screen.getByRole("button", { name: "Try again" }));

    expect(await line()).toHaveTextContent(/^Requested by cab_coffee/);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("Approve", () => {
  test("sends the approval of that request; the answer is what the block shows, and a toast says so", async () => {
    const { api, user } = show(requested(), { [APPROVE]: json(200, approved()) });

    await user.click(await screen.findByRole("button", { name: "Approve" }));

    expect(await screen.findByText("Request approved")).toBeInTheDocument();
    expect(api.lastCall(APPROVE)?.path).toBe(`/api/admin/broadcasts/${BROADCAST_ID}/approve`);
    expect(api.lastCall(APPROVE)?.rawBody).toBe("");
    expect(await line()).toHaveTextContent("Approved. cab_coffee can go live.");
    expect(offered(await block())).toEqual(["Refuse"]);
    // The answer is the live stream: it was not asked for a second time.
    expect(api.calls(BROADCAST)).toHaveLength(1);
    // Nothing opens to confirm an approval.
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  test("the focus goes to the heading of the block when the answer took Approve away", async () => {
    const { user } = show(requested(), { [APPROVE]: json(200, approved()) });

    await user.click(await screen.findByRole("button", { name: "Approve" }));

    await waitFor(() => expect(heading()).toHaveFocus());
  });

  test("while its request is out Approve is busy and Refuse waits; a second tap sends nothing", async () => {
    const answer = deferred();
    const { api, user } = show(requested(), { [APPROVE]: answer.handler });
    const approve = await screen.findByRole("button", { name: "Approve" });

    await user.click(approve);
    await user.click(approve);

    await waitFor(() => expect(approve).toHaveAttribute("aria-busy", "true"));
    expect(screen.getByRole("button", { name: "Refuse" })).toBeDisabled();
    expect(api.calls(APPROVE)).toHaveLength(1);

    answer.resolve(json(200, approved()));

    expect(await screen.findByText("Request approved")).toBeInTheDocument();
    expect(api.calls(APPROVE)).toHaveLength(1);
  });

  test("a refusal of the API stands above the buttons, and the block is read again", async () => {
    const { api, user } = show(requested(), { [APPROVE]: apiError(409, "CONFLICT", DECIDED) });
    const approve = await screen.findByRole("button", { name: "Approve" });
    // Another admin approved it in the meantime.
    api.on(BROADCAST, json(200, approved()));

    await user.click(approve);

    const region = await block();
    const refusal = await within(region).findByRole("alert");
    expect(refusal).toHaveTextContent(DECIDED);
    await waitFor(async () =>
      expect(await line()).toHaveTextContent("Approved. cab_coffee can go live."),
    );
    expect(api.calls(BROADCAST)).toHaveLength(2);
    // The message stands above the buttons that are left.
    const refuse = within(region).getByRole("button", { name: "Refuse" });
    expect(refusal.compareDocumentPosition(refuse) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.queryByText("Request approved")).not.toBeInTheDocument();
  });

  test("the refusal gives way to what the admin does next", async () => {
    const { api, user } = show(requested(), { [APPROVE]: apiError(409, "CONFLICT", DECIDED) });
    const approve = await screen.findByRole("button", { name: "Approve" });
    api.on(BROADCAST, json(200, approved()));
    await user.click(approve);
    const region = await block();
    await within(region).findByRole("alert");

    await user.click(await within(region).findByRole("button", { name: "Refuse" }));

    // Looked for in the page itself: an open dialog hides what is behind it from a query by role.
    const dialog = await screen.findByRole("alertdialog", { name: "Refuse request" });
    expect(region.querySelector("[data-slot=submit-error]")).not.toBeInTheDocument();
    expect(region).not.toHaveTextContent(DECIDED);

    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await gone();
    expect(within(region).queryByRole("alert")).not.toBeInTheDocument();
  });

  test("after RATE_LIMITED Approve waits for the time the API names, with the countdown beside the message", async () => {
    const { api, user } = show(requested(), {
      [APPROVE]: apiError(429, "RATE_LIMITED", "Too many requests. Try again in a moment.", {
        retry_after_seconds: 30,
      }),
    });

    await user.click(await screen.findByRole("button", { name: "Approve" }));

    const region = await block();
    expect(await within(region).findByRole("alert")).toHaveTextContent(
      "Too many requests. Try again in a moment.",
    );
    expect(within(region).getByText(/^Try again in \d+ s$/)).toBeInTheDocument();
    expect(within(region).getByRole("button", { name: "Approve" })).toBeDisabled();
    // The request was not carried out, and the block is as it was: nothing is read again.
    expect(api.calls(BROADCAST)).toHaveLength(1);
    expect(offered(region)).toEqual(["Approve", "Refuse"]);
  });

  test("without an answer the block is read again before Approve is free, and the banner says that nobody knows", async () => {
    const reread = deferred();
    const { api, user } = show(requested(), { [APPROVE]: noAnswer });
    const approve = await screen.findByRole("button", { name: "Approve" });
    api.on(BROADCAST, reread.handler);

    await user.click(approve);

    await waitFor(() => expect(api.calls(BROADCAST)).toHaveLength(2));
    expect(approve).toHaveAttribute("aria-busy", "true");

    // The approval did not arrive: the block shows the request as the server holds it.
    reread.resolve(json(200, requested()));

    expect(await within(await block()).findByRole("alert")).toHaveTextContent(
      "No connection. Try again.",
    );
    expect(screen.getByRole("button", { name: "Approve" })).toBeEnabled();
    expect(api.calls(APPROVE)).toHaveLength(1);
  });
});

describe("Refuse", () => {
  async function openRefusal(user: ReturnType<typeof renderWithProviders>["user"]) {
    await user.click(await screen.findByRole("button", { name: "Refuse" }));
    return screen.findByRole("alertdialog", { name: "Refuse request" });
  }

  test("asks for a reason in a confirmation that says who reads it, behind a destructive button", async () => {
    const { api, user } = show(requested(), { [REFUSE]: json(200, refused()) });
    const dialog = await openRefusal(user);

    expect(dialog).toHaveAccessibleDescription("The requester sees your reason.");
    const confirm = within(dialog).getByRole("button", { name: "Refuse request" });
    expect(confirm).toHaveAttribute("data-variant", "destructive");
    // Nothing is refused without a reason.
    expect(confirm).toBeDisabled();
    expect(within(dialog).getByLabelText("Reason")).toHaveAttribute("maxlength", "500");
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Close" })).toBeInTheDocument();
    expect(api.calls(REFUSE)).toHaveLength(0);
  });

  test("sends the reason without the blanks around it; the answer closes the dialog, says so in a toast and is what the block shows", async () => {
    const { api, user } = show(requested(), { [REFUSE]: json(200, refused()) });
    const dialog = await openRefusal(user);

    await user.type(within(dialog).getByLabelText("Reason"), "  The event is not public.  ");
    await user.click(within(dialog).getByRole("button", { name: "Refuse request" }));

    await gone();
    expect(await screen.findByText("Request refused")).toBeInTheDocument();
    expect(api.lastCall(REFUSE)?.path).toBe(`/api/admin/broadcasts/${BROADCAST_ID}/refuse`);
    expect(api.lastCall(REFUSE)?.body).toEqual({ reason: "The event is not public." });
    expect(await line()).toHaveTextContent("No live stream.");
    expect(offered(await block())).toEqual(["Start a live stream"]);
    expect(api.calls(BROADCAST)).toHaveLength(1);
    // "Refuse" is gone: the focus goes to the heading of the block.
    await waitFor(() => expect(heading()).toHaveFocus());
  });

  test("an approved request is refused the same way", async () => {
    const { api, user } = show(approved(), { [REFUSE]: json(200, refused()) });
    const dialog = await openRefusal(user);

    await user.type(within(dialog).getByLabelText("Reason"), "The speaker has cancelled.");
    await user.click(within(dialog).getByRole("button", { name: "Refuse request" }));

    await gone();
    expect(api.lastCall(REFUSE)?.body).toEqual({ reason: "The speaker has cancelled." });
  });

  test("a refusal of the API is shown in the dialog, which stays open, and the block behind it is read again", async () => {
    const { api, user } = show(approved(), {
      [REFUSE]: apiError(409, "CONFLICT", "This live stream is on. End it instead."),
    });
    const dialog = await openRefusal(user);
    // The requester went live in the meantime.
    api.on(BROADCAST, json(200, live()));

    await user.type(within(dialog).getByLabelText("Reason"), "The speaker has cancelled.");
    await user.click(within(dialog).getByRole("button", { name: "Refuse request" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "This live stream is on. End it instead.",
    );
    expect(screen.getByRole("alertdialog", { name: "Refuse request" })).toBeInTheDocument();
    await waitFor(() => expect(api.calls(BROADCAST)).toHaveLength(2));
    expect(screen.queryByText("Request refused")).not.toBeInTheDocument();

    // Closed, the block shows what the stream is by now.
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await gone();
    expect(await line()).toHaveTextContent("Live since 11:45.");
    expect(offered(await block())).toEqual(["Watch", "End stream"]);
  });

  test("a reason the API does not take is said on the field, and holds until the reason changes", async () => {
    const { user } = show(requested(), {
      [REFUSE]: apiError(422, "VALIDATION_ERROR", "Give a reason of at most 500 characters.", {
        field: "reason",
      }),
    });
    const dialog = await openRefusal(user);
    const reason = within(dialog).getByLabelText("Reason");

    await user.type(reason, "No.");
    await user.click(within(dialog).getByRole("button", { name: "Refuse request" }));

    await waitFor(() => expect(reason).toBeInvalid());
    expect(reason).toHaveAccessibleDescription(/Give a reason of at most 500 characters\./);
    expect(within(dialog).getAllByRole("alert")).toHaveLength(1);

    await user.type(reason, " The market closes first.");
    expect(reason).toBeValid();
  });

  test("Cancel sends nothing and gives the focus back to Refuse; the next opening starts empty", async () => {
    const { api, user } = show(requested());
    const dialog = await openRefusal(user);
    await user.type(within(dialog).getByLabelText("Reason"), "Not this one.");

    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

    await gone();
    expect(api.calls(REFUSE)).toHaveLength(0);
    await waitFor(() => expect(screen.getByRole("button", { name: "Refuse" })).toHaveFocus());

    const again = await openRefusal(user);
    expect(within(again).getByLabelText("Reason")).toHaveValue("");
  });

  test("while the request is out the dialog cannot be closed, and a second tap sends nothing", async () => {
    const answer = deferred();
    const { api, user } = show(requested(), { [REFUSE]: answer.handler });
    const dialog = await openRefusal(user);

    await user.type(within(dialog).getByLabelText("Reason"), "The event is not public.");
    await user.click(within(dialog).getByRole("button", { name: "Refuse request" }));
    await user.click(within(dialog).getByRole("button", { name: "Refuse request" }));
    await user.keyboard("{Escape}");

    expect(within(dialog).getByRole("button", { name: "Refuse request" })).toHaveAttribute(
      "aria-busy",
      "true",
    );
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toBeDisabled();
    expect(within(dialog).getByRole("button", { name: "Close" })).toBeDisabled();
    expect(screen.getByRole("alertdialog", { name: "Refuse request" })).toBeInTheDocument();
    expect(api.calls(REFUSE)).toHaveLength(1);

    answer.resolve(json(200, refused()));
    await gone();
  });
});

describe("End stream", () => {
  async function openEnd(user: ReturnType<typeof renderWithProviders>["user"]) {
    await user.click(await screen.findByRole("button", { name: "End stream" }));
    return screen.findByRole("alertdialog", { name: "End the live stream?" });
  }

  test("is confirmed in the dialog of the studio, behind a destructive button", async () => {
    const { api, user } = show(live());
    const dialog = await openEnd(user);

    expect(dialog).toHaveAccessibleDescription(
      "Viewers see that it has ended. No picture and no sound is kept.",
    );
    expect(within(dialog).getByRole("button", { name: "End stream" })).toHaveAttribute(
      "data-variant",
      "destructive",
    );
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Close" })).toBeInTheDocument();
    // The tap on the block's button ends nothing by itself.
    expect(api.calls(END)).toHaveLength(0);
  });

  test("sends the end of that stream; the answer closes the dialog, says so in a toast and is what the block shows", async () => {
    const { api, user } = show(live(), { [END]: json(200, ended()) });
    const dialog = await openEnd(user);

    await user.click(within(dialog).getByRole("button", { name: "End stream" }));

    await gone();
    expect(await screen.findByText("Live stream ended")).toBeInTheDocument();
    expect(api.lastCall(END)?.path).toBe(`/api/broadcasts/${BROADCAST_ID}/end`);
    expect(api.lastCall(END)?.rawBody).toBe("");
    expect(await line()).toHaveTextContent("The last live stream ended on 11 Oct 2026, 11:58.");
    expect(offered(await block())).toEqual(["Start a live stream"]);
    expect(api.calls(BROADCAST)).toHaveLength(1);
    await waitFor(() => expect(heading()).toHaveFocus());
  });

  test("a refusal of the API is shown in the dialog, and the block is read again", async () => {
    const { api, user } = show(live(), {
      [END]: apiError(403, "FORBIDDEN", "You are not allowed to do this."),
    });
    const dialog = await openEnd(user);

    await user.click(within(dialog).getByRole("button", { name: "End stream" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "You are not allowed to do this.",
    );
    expect(screen.getByRole("alertdialog", { name: "End the live stream?" })).toBeInTheDocument();
    await waitFor(() => expect(api.calls(BROADCAST)).toHaveLength(2));
    expect(screen.queryByText("Live stream ended")).not.toBeInTheDocument();
  });

  test("without an answer the block is read again before the dialog lets go, and shows what the server holds", async () => {
    const reread = deferred();
    const { api, user } = show(live(), { [END]: noAnswer });
    const dialog = await openEnd(user);
    api.on(BROADCAST, reread.handler);

    await user.click(within(dialog).getByRole("button", { name: "End stream" }));

    await waitFor(() => expect(api.calls(BROADCAST)).toHaveLength(2));
    expect(within(dialog).getByRole("button", { name: "End stream" })).toHaveAttribute(
      "aria-busy",
      "true",
    );
    expect(within(dialog).getByRole("button", { name: "Close" })).toBeDisabled();

    // The end did arrive: the block behind the dialog says so.
    reread.resolve(json(200, ended()));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent("No connection. Try again.");
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await gone();
    expect(await line()).toHaveTextContent("The last live stream ended on 11 Oct 2026, 11:58.");
    expect(api.calls(END)).toHaveLength(1);
  });
});

describe("what changes without a tap", () => {
  test("it names the live stream of its market to the event stream, and a broadcast.changed brings a request without a reload", async () => {
    const streams = installFakeEventSource();
    const { api } = show(
      nothing(),
      {},
      {
        live: true,
        // An announcement is read at once.
        config: config({ live_announce_spread_ms: 0 }),
      },
    );

    expect(streams.latest().topics).toEqual([`broadcast:${MARKET_ID}`]);
    expect(await line()).toHaveTextContent("No live stream.");
    // The stream is open: what was pushed before is not sent again, so the answer is read once more.
    act(() => streams.latest().send(readyEvent()));
    await waitFor(() => expect(api.calls(BROADCAST)).toHaveLength(2));

    // The proposer asks for a live stream.
    api.on(BROADCAST, json(200, requested()));
    act(() => streams.latest().send(broadcastChangedEvent()));

    await waitFor(async () => expect(await line()).toHaveTextContent(/^Requested by cab_coffee/));
    expect(offered(await block())).toEqual(["Approve", "Refuse"]);
    // One read for the announcement: the block itself asks for nothing.
    expect(api.calls(BROADCAST)).toHaveLength(3);
  });

  test("when the status of the market changes in the cache, the block reads its answer again: after Approve on a proposal it is there", async () => {
    const client = createTestQueryClient();
    const proposal = adminMarketDetail({ status: "PROPOSED", trading_open: false });
    client.setQueryData(keys.markets.detail(MARKET_ID), proposal);
    const { api } = show(apiError(404, "NOT_FOUND", "Not found."), {}, { client });
    await waitFor(() => expect(api.calls(BROADCAST)).toHaveLength(1));
    expect(screen.queryByRole("heading")).not.toBeInTheDocument();

    // The answer of the admin's approval is the market, and the cache holds it.
    api.on(BROADCAST, json(200, nothing()));
    act(() => {
      client.setQueryData(keys.markets.detail(MARKET_ID), { ...proposal, status: "OPEN" });
    });

    expect(await line()).toHaveTextContent("No live stream.");
    expect(offered(await block())).toEqual(["Start a live stream"]);
    expect(api.calls(BROADCAST)).toHaveLength(2);

    // A market that is written again with the status it has asks for nothing.
    act(() => {
      client.setQueryData(keys.markets.detail(MARKET_ID), {
        ...proposal,
        status: "OPEN",
        volume_cr: "12",
      });
    });
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(api.calls(BROADCAST)).toHaveLength(2);
    // The market itself is the screen's to read: the block never asks for it.
    expect(api.requests.every((request) => request.path.endsWith("/broadcast"))).toBe(true);
  });
});
