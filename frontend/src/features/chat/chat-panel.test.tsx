import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";

import { keys } from "@/lib/api/keys";
import type { ChatResponse } from "@/lib/api/schema";
import { installFakeEventSource } from "@/test/fake-event-source";
import {
  BROADCAST_ID,
  broadcastChangedEvent,
  chat,
  CHAT_MESSAGE_ID,
  chatMessage,
  chatMessageEvent,
  chatMessageRemoved,
  chatRemovedEvent,
  closedChat,
  config,
  MARKET_ID,
  OTHER_CHAT_MESSAGE_ID,
  OTHER_MARKET_ID,
  readyEvent,
} from "@/test/fixtures";
import { apiError, deferred, json, mockApi, noAnswer, type Route } from "@/test/http";
import { watchScrolling } from "@/test/observers";
import { createTestQueryClient, renderWithProviders } from "@/test/render";

import { ChatPanel, type ChatPanelProps } from "./index";

const CHAT: Route = "GET /api/markets/{market_id}/chat";
const POST: Route = "POST /api/markets/{market_id}/chat";
const REMOVE: Route = "POST /api/admin/markets/{market_id}/chat/{message_id}/remove";

/** A live stream of the same market that is not the one on screen. */
const OTHER_BROADCAST_ID = "0e9d8c7b-6a5f-4e3d-9c2b-1a0f9e8d7c6b";

const FIRST = "Is that the upper station?";
const SECOND = "Yes, and the cabin leaves in a minute.";
/** What the viewer of the tests writes, and the message the API stores for it. */
const TYPED = "There it goes.";
const stored = chatMessage({
  id: "f3a5b7c9-6d8e-4b0f-a1c2-4d6e8f0a2b3c",
  username: "anna_m",
  text: TYPED,
});

type Surroundings = Parameters<typeof renderWithProviders>[1];

function renderPanel(props: Partial<ChatPanelProps> = {}, surroundings: Surroundings = {}) {
  return renderWithProviders(
    <ChatPanel marketId={MARKET_ID} broadcastId={BROADCAST_ID} {...props} />,
    surroundings,
  );
}

/** The panel and the rows of its log, also while a dialog over them hides them from a reader. */
const panel = () => document.querySelector<HTMLElement>('[data-slot="chat-panel"]')!;
const rows = () => [...document.querySelectorAll<HTMLElement>('[data-slot="chat-log"] li')];
const texts = () => rows().map((row) => row.textContent);
const field = () => screen.getByRole("textbox", { name: "Message" });
const sendButton = () => screen.getByRole("button", { name: "Send" });

describe("the five states of the panel", () => {
  test("loading: six skeleton bars in the log, and the composer disabled", async () => {
    const answer = deferred();
    mockApi({ [CHAT]: answer.handler });
    renderPanel();

    expect(panel()).toHaveAttribute("data-state", "loading");
    const bars = panel().querySelectorAll('[data-slot="chat-log-skeleton"] [data-slot="skeleton"]');
    expect(bars).toHaveLength(6);
    expect(new Set([...bars].map((bar) => bar.className)).size).toBe(2);
    expect(field()).toBeDisabled();
    expect(sendButton()).toBeDisabled();
    expect(screen.queryByRole("log")).not.toBeInTheDocument();

    answer.resolve(json(200, chat()));
    expect(await screen.findByText(FIRST)).toBeInTheDocument();
    expect(field()).toBeEnabled();
  });

  test("empty: the sentence stands in the log, and the composer is ready", async () => {
    mockApi({ [CHAT]: json(200, chat({ items: [] })) });
    renderPanel();

    expect(await screen.findByText("No messages yet.")).toBeInTheDocument();
    expect(within(screen.getByRole("log")).getByText("No messages yet.")).toBeInTheDocument();
    expect(rows()).toHaveLength(0);
    expect(field()).toBeEnabled();
  });

  test("closed: the sentence stands in place of the rows, and there is no composer", async () => {
    mockApi({ [CHAT]: json(200, closedChat()) });
    renderPanel();

    expect(await screen.findByText("The live chat is closed.")).toBeInTheDocument();
    expect(panel()).toHaveAttribute("data-state", "closed");
    expect(screen.queryByRole("textbox", { name: "Message" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Send" })).not.toBeInTheDocument();
  });

  test("error: the API's message with Try again in the log, and the composer stays", async () => {
    const api = mockApi({ [CHAT]: apiError(500, "INTERNAL_ERROR", "Something broke.") });
    const { user } = renderPanel();

    expect(await screen.findByRole("alert")).toHaveTextContent("Something broke.");
    expect(field()).toBeEnabled();

    api.on(CHAT, json(200, chat()));
    await user.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findByText(FIRST)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  test("content: one row per message, in the order of the answer", async () => {
    mockApi({ [CHAT]: json(200, chat()) });
    renderPanel();

    await screen.findByText(FIRST);
    expect(panel()).toHaveAttribute("data-state", "open");
    expect(texts()).toEqual([`cab_coffee ${FIRST}`, `vis_moritz VIS ${SECOND}`]);
  });

  test("a market the viewer cannot see: the panel is absent", async () => {
    const api = mockApi({ [CHAT]: apiError(404, "NOT_FOUND", "This market does not exist.") });
    const { container } = renderPanel();

    await waitFor(() => expect(api.calls(CHAT)).toHaveLength(1));
    await waitFor(() => expect(container.querySelector('[data-slot="chat-panel"]')).toBeNull());
    expect(screen.queryByRole("region", { name: "Live chat" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading")).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("what the panel is for a screen reader", () => {
  test("a region and a log, both named Live chat, under a heading of the second level", async () => {
    mockApi({ [CHAT]: json(200, chat()) });
    renderPanel();

    await screen.findByText(FIRST);
    expect(screen.getByRole("heading", { level: 2, name: "Live chat" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Live chat" })).toBeInTheDocument();
    expect(screen.getByRole("log", { name: "Live chat" })).toBeInTheDocument();
  });
});

describe("a row", () => {
  test("carries the badge VIS where the server says an admin wrote it, and nowhere else", async () => {
    mockApi({ [CHAT]: json(200, chat()) });
    renderPanel();

    await screen.findByText(FIRST);
    const [player, admin] = rows();
    expect(within(player!).queryByText("VIS")).not.toBeInTheDocument();
    expect(within(admin!).getByText("VIS")).toHaveAttribute("data-slot", "vis-badge");
  });

  test("a name that looks like VIS's gets no badge: nothing is read from a username", async () => {
    mockApi({
      [CHAT]: json(200, chat({ items: [chatMessage({ username: "VIS", from_vis: false })] })),
    });
    renderPanel();

    await screen.findByText(FIRST);
    expect(panel().querySelector('[data-slot="vis-badge"]')).toBeNull();
  });

  test("shows what was written as text: no markup, no picture and no link", async () => {
    const text = "<img src=x onerror=alert(1)> <b>bold</b> https://example.org";
    mockApi({ [CHAT]: json(200, chat({ items: [chatMessage({ text })] })) });
    renderPanel();

    const line = (await screen.findByText(text, { exact: false })).closest("p")!;
    expect(line).toHaveTextContent(`cab_coffee ${text}`);
    expect(panel().querySelector("img, a, b")).toBeNull();
    // It wraps a string without a space, and its row clips what would leave it.
    expect(line).toHaveClass("wrap-anywhere", "min-w-0");
    expect(line.closest("li")).toHaveClass("overflow-hidden");
  });
});

describe("sending a message", () => {
  test("the field takes the length of /config and counts what is typed", async () => {
    mockApi({ [CHAT]: json(200, chat()) });
    const { user } = renderPanel({}, { config: config({ chat_message_max_length: 12 }) });

    await screen.findByText(FIRST);
    expect(field()).toHaveAttribute("maxLength", "12");
    expect(field()).toHaveAttribute("placeholder", "Write a message");
    expect(panel().querySelector('[data-slot="chat-count"]')).toHaveTextContent("0 / 12");
    expect(sendButton()).toBeDisabled();

    await user.type(field(), "Hello there, everybody");

    expect(field()).toHaveValue("Hello there,");
    expect(panel().querySelector('[data-slot="chat-count"]')).toHaveTextContent("12 / 12");
    expect(sendButton()).toBeEnabled();
  });

  test("Enter sends the text as typed, the field empties after the answer, and the message stands in the log", async () => {
    const api = mockApi({ [CHAT]: json(200, chat()), [POST]: json(201, stored) });
    const { user } = renderPanel();

    await screen.findByText(FIRST);
    await user.type(field(), `  ${TYPED}{Enter}`);

    expect(await screen.findByText(TYPED, { exact: false })).toBeInTheDocument();
    expect(api.calls(POST)).toHaveLength(1);
    expect(api.lastCall(POST)?.body).toEqual({ text: `  ${TYPED}` });
    expect(api.lastCall(POST)?.path).toBe(`/api/markets/${MARKET_ID}/chat`);
    expect(field()).toHaveValue("");
    expect(texts().at(-1)).toBe(`anna_m ${TYPED}`);
    // The answer is the message: the chat is not read for it.
    expect(api.calls(CHAT)).toHaveLength(1);
  });

  test("the button sends too, and from the tap until the answer nothing is sent again", async () => {
    const answer = deferred();
    const api = mockApi({ [CHAT]: json(200, chat()), [POST]: answer.handler });
    const { user } = renderPanel();

    await screen.findByText(FIRST);
    await user.type(field(), TYPED);
    await user.click(sendButton());

    await waitFor(() => expect(sendButton()).toBeDisabled());
    expect(field()).toHaveValue(TYPED);
    expect(field()).toHaveAttribute("readonly");
    await user.type(field(), "{Enter}");
    expect(api.calls(POST)).toHaveLength(1);

    answer.resolve(json(201, stored));
    await waitFor(() => expect(field()).toHaveValue(""));
    expect(field()).not.toHaveAttribute("readonly");
  });

  test("Enter and a tap in the same moment are one request", async () => {
    const api = mockApi({ [CHAT]: json(200, chat()), [POST]: json(201, stored) });
    const { user } = renderPanel();

    await screen.findByText(FIRST);
    await user.type(field(), TYPED);
    const form = field().closest("form")!;
    act(() => {
      fireEvent.submit(form);
      fireEvent.submit(form);
    });

    await waitFor(() => expect(field()).toHaveValue(""));
    expect(api.calls(POST)).toHaveLength(1);
  });

  test("a message whose event arrives before its answer shows once", async () => {
    const streams = installFakeEventSource();
    const answer = deferred();
    mockApi({ [CHAT]: json(200, chat()), [POST]: answer.handler });
    const { user } = renderPanel({}, { live: true });

    await screen.findByText(FIRST);
    await user.type(field(), `${TYPED}{Enter}`);
    act(() => streams.latest().send(chatMessageEvent({ message: stored })));
    expect(await screen.findByText(TYPED, { exact: false })).toBeInTheDocument();

    answer.resolve(json(201, stored));
    await waitFor(() => expect(field()).toHaveValue(""));
    expect(texts()).toEqual([`cab_coffee ${FIRST}`, `vis_moritz VIS ${SECOND}`, `anna_m ${TYPED}`]);
  });

  test("a message whose answer arrives before its event shows once", async () => {
    const streams = installFakeEventSource();
    mockApi({ [CHAT]: json(200, chat()), [POST]: json(201, stored) });
    const { user } = renderPanel({}, { live: true });

    await screen.findByText(FIRST);
    await user.type(field(), `${TYPED}{Enter}`);
    await waitFor(() => expect(field()).toHaveValue(""));
    act(() => streams.latest().send(chatMessageEvent({ message: stored })));

    await waitFor(() => expect(rows()).toHaveLength(3));
    expect(texts().at(-1)).toBe(`anna_m ${TYPED}`);
  });

  test("RATE_LIMITED: the API's sentence under the field, and sending is off for the countdown", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const api = mockApi({
        [CHAT]: json(200, chat()),
        [POST]: apiError(429, "RATE_LIMITED", "You are writing too fast.", {
          retry_after_seconds: 30,
        }),
      });
      const { user } = renderPanel();

      await screen.findByText(FIRST);
      await user.type(field(), `${TYPED}{Enter}`);

      expect(await screen.findByRole("alert")).toHaveTextContent("You are writing too fast.");
      expect(screen.getByText("Try again in 30 s")).toBeInTheDocument();
      expect(field()).toHaveValue(TYPED);
      expect(sendButton()).toBeDisabled();

      // What is typed meanwhile does not end the wait, and Enter sends nothing.
      await user.type(field(), "!{Enter}");
      expect(screen.getByRole("alert")).toHaveTextContent("You are writing too fast.");
      expect(sendButton()).toBeDisabled();
      expect(api.calls(POST)).toHaveLength(1);

      act(() => void vi.advanceTimersByTime(30_000));

      expect(screen.queryByText(/Try again in/)).not.toBeInTheDocument();
      expect(sendButton()).toBeEnabled();

      api.on(POST, json(201, stored));
      await user.type(field(), "{Enter}");
      await waitFor(() => expect(field()).toHaveValue(""));
      expect(api.lastCall(POST)?.body).toEqual({ text: `${TYPED}!` });
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  test("VALIDATION_ERROR: the sentence stands under the field until the text changes", async () => {
    mockApi({
      [CHAT]: json(200, chat()),
      [POST]: apiError(422, "VALIDATION_ERROR", "Write a message.", { field: "text" }),
    });
    const { user } = renderPanel();

    await screen.findByText(FIRST);
    await user.type(field(), " {Enter}");

    const sentence = await screen.findByRole("alert");
    expect(sentence).toHaveTextContent("Write a message.");
    expect(field()).toBeInvalid();
    expect(field()).toHaveAccessibleDescription("Write a message.");
    expect(field()).toHaveValue(" ");

    await user.type(field(), "x");

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(field()).toBeValid();
  });

  test("CONFLICT: the chat is closed, so the panel shows its closed state and no composer", async () => {
    const api = mockApi({
      [CHAT]: json(200, chat()),
      [POST]: apiError(409, "CONFLICT", "The live chat is closed."),
    });
    const { user } = renderPanel();

    await screen.findByText(FIRST);
    // The chat is read again after the refusal; here it still says what it said.
    await user.type(field(), `${TYPED}{Enter}`);

    await waitFor(() => expect(panel()).toHaveAttribute("data-state", "closed"));
    expect(within(screen.getByRole("log")).getByText("The live chat is closed.")).toBeVisible();
    expect(screen.queryByRole("textbox", { name: "Message" })).not.toBeInTheDocument();
    expect(rows()).toHaveLength(0);
    await waitFor(() => expect(api.calls(CHAT)).toHaveLength(2));
    expect(panel()).toHaveAttribute("data-state", "closed");
  });

  test("no answer: the chat is read again, and the field keeps its text", async () => {
    const api = mockApi({ [CHAT]: json(200, chat()), [POST]: noAnswer });
    const { user } = renderPanel();

    await screen.findByText(FIRST);
    // The message was taken although no answer arrived: the read shows it.
    api.on(CHAT, json(200, chat({ items: [...chat().items, stored] })));
    await user.type(field(), `${TYPED}{Enter}`);

    expect(await screen.findByRole("alert")).toHaveTextContent("No connection. Try again.");
    expect(api.calls(CHAT)).toHaveLength(2);
    expect(field()).toHaveValue(TYPED);
    expect(sendButton()).toBeEnabled();
    expect(texts().at(-1)).toBe(`anna_m ${TYPED}`);
  });
});

describe("removing a message", () => {
  const removable = () => chat({ can_remove: true });

  test("a viewer who removes sees a button per row, named by its writer; nobody else sees one", async () => {
    mockApi({ [CHAT]: json(200, removable()) });
    const { unmount } = renderPanel();

    await screen.findByText(FIRST);
    expect(
      screen.getByRole("button", { name: "Remove the message from cab_coffee" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Remove the message from vis_moritz" }),
    ).toBeInTheDocument();
    unmount();

    mockApi({ [CHAT]: json(200, chat()) });
    renderPanel();
    await screen.findByText(FIRST);
    expect(screen.queryByRole("button", { name: /^Remove the message/ })).not.toBeInTheDocument();
  });

  test("the confirmation states the consequence and shows the line; confirming removes it for good", async () => {
    const api = mockApi({
      [CHAT]: json(200, removable()),
      [REMOVE]: json(200, chatMessageRemoved()),
    });
    const { user } = renderPanel();

    await screen.findByText(FIRST);
    await user.click(screen.getByRole("button", { name: "Remove the message from cab_coffee" }));

    const dialog = await screen.findByRole("alertdialog", { name: "Remove this message?" });
    expect(dialog).toHaveTextContent("It disappears for everyone. This cannot be taken back.");
    expect(within(dialog).getByText(FIRST, { exact: false })).toHaveTextContent(
      `cab_coffee ${FIRST}`,
    );
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Close" })).toBeInTheDocument();
    expect(api.calls(REMOVE)).toHaveLength(0);

    await user.click(within(dialog).getByRole("button", { name: "Remove message" }));

    expect(await screen.findByText("Message removed")).toBeInTheDocument();
    expect(api.lastCall(REMOVE)?.path).toBe(
      `/api/admin/markets/${MARKET_ID}/chat/${CHAT_MESSAGE_ID}/remove`,
    );
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(texts()).toEqual([`vis_moritz VIS ${SECOND}`]);
    // The row went with its button: the heading of the panel has the focus.
    await waitFor(() => expect(screen.getByRole("heading", { name: "Live chat" })).toHaveFocus());
  });

  test("Cancel sends nothing and gives the focus back to the button of the row", async () => {
    const api = mockApi({ [CHAT]: json(200, removable()) });
    const { user } = renderPanel();

    await screen.findByText(FIRST);
    const remove = screen.getByRole("button", { name: "Remove the message from vis_moritz" });
    await user.click(remove);
    await user.click(await screen.findByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(api.calls(REMOVE)).toHaveLength(0);
    expect(rows()).toHaveLength(2);
    await waitFor(() => expect(remove).toHaveFocus());
  });

  test("the dialog cannot be closed while its request is out", async () => {
    const answer = deferred();
    mockApi({ [CHAT]: json(200, removable()), [REMOVE]: answer.handler });
    const { user } = renderPanel();

    await screen.findByText(FIRST);
    await user.click(screen.getByRole("button", { name: "Remove the message from cab_coffee" }));
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "Remove message" }));

    await waitFor(() =>
      expect(within(dialog).getByRole("button", { name: "Cancel" })).toBeDisabled(),
    );
    expect(within(dialog).getByRole("button", { name: "Close" })).toBeDisabled();
    await user.keyboard("{Escape}");
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();

    answer.resolve(json(200, chatMessageRemoved()));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
  });

  test("NOT_FOUND: the API's sentence in the dialog, and the chat is read again", async () => {
    const api = mockApi({
      [CHAT]: json(200, removable()),
      [REMOVE]: apiError(404, "NOT_FOUND", "This message does not exist."),
    });
    const { user } = renderPanel();

    await screen.findByText(FIRST);
    api.on(CHAT, json(200, chat({ can_remove: true, items: [chat().items[1]!] })));
    await user.click(screen.getByRole("button", { name: "Remove the message from cab_coffee" }));
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "Remove message" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "This message does not exist.",
    );
    await waitFor(() => expect(api.calls(CHAT)).toHaveLength(2));
    await waitFor(() => expect(texts()).toEqual([`vis_moritz VIS ${SECOND}`]));
    expect(screen.queryByText("Message removed")).not.toBeInTheDocument();
  });
});

describe("following the event stream", () => {
  let streams: ReturnType<typeof installFakeEventSource>;
  /** An announcement is read at once. */
  const live = { live: true, config: config({ live_announce_spread_ms: 0 }) };

  beforeEach(() => {
    streams = installFakeEventSource();
    // The first poll after a loss comes late in its interval, unless a test says otherwise.
    vi.spyOn(Math, "random").mockReturnValue(0.99);
  });

  test("the panel names the topic of its market", async () => {
    mockApi({ [CHAT]: json(200, chat()) });
    renderPanel({}, live);

    await screen.findByText(FIRST);
    expect(streams.latest().topics).toEqual([`market:${MARKET_ID}`]);
  });

  test("chat.message puts the message at the end of the log and asks for nothing", async () => {
    const api = mockApi({ [CHAT]: json(200, chat()) });
    renderPanel({}, live);

    await screen.findByText(FIRST);
    act(() => streams.latest().send(chatMessageEvent()));

    expect(await screen.findByText(TYPED, { exact: false })).toBeInTheDocument();
    expect(texts()).toEqual([`cab_coffee ${FIRST}`, `vis_moritz VIS ${SECOND}`, `anna_m ${TYPED}`]);
    expect(api.calls(CHAT)).toHaveLength(1);
  });

  test("a message of another live stream, and one of another market, is dropped", async () => {
    mockApi({ [CHAT]: json(200, chat()) });
    renderPanel({}, live);

    await screen.findByText(FIRST);
    act(() => {
      streams.latest().send(chatMessageEvent({ broadcast_id: OTHER_BROADCAST_ID }));
      streams.latest().send(chatMessageEvent({ market_id: OTHER_MARKET_ID }));
      streams.latest().send(
        chatMessageEvent({
          message: chatMessage({ id: "0a0a0a0a-1111-4222-8333-444444444444", text: "Last." }),
        }),
      );
    });

    expect(await screen.findByText("Last.", { exact: false })).toBeInTheDocument();
    expect(screen.queryByText(TYPED, { exact: false })).not.toBeInTheDocument();
    expect(rows()).toHaveLength(3);
  });

  test("chat.removed takes the row of that id out, for whoever has it on screen", async () => {
    const api = mockApi({ [CHAT]: json(200, chat()) });
    renderPanel({}, live);

    await screen.findByText(FIRST);
    act(() => streams.latest().send(chatRemovedEvent({ market_id: OTHER_MARKET_ID })));
    act(() => streams.latest().send(chatRemovedEvent()));

    await waitFor(() => expect(texts()).toEqual([`vis_moritz VIS ${SECOND}`]));
    expect(api.calls(CHAT)).toHaveLength(1);
  });

  test("the log never holds more than chat_backlog_size: the earliest leave at the top", async () => {
    mockApi({ [CHAT]: json(200, chat()) });
    renderPanel({}, { live: true, config: config({ chat_backlog_size: 3 }) });

    await screen.findByText(FIRST);
    const sent = ["One.", "Two.", "Three."].map((text, index) =>
      chatMessageEvent({
        message: chatMessage({ id: `0b0b0b0b-1111-4222-8333-44444444444${index}`, text }),
      }),
    );
    act(() => streams.latest().send(sent[0]!));
    await waitFor(() =>
      expect(texts()).toEqual([
        `cab_coffee ${FIRST}`,
        `vis_moritz VIS ${SECOND}`,
        "cab_coffee One.",
      ]),
    );

    act(() => {
      streams.latest().send(sent[1]!);
      streams.latest().send(sent[2]!);
    });
    await waitFor(() =>
      expect(texts()).toEqual(["cab_coffee One.", "cab_coffee Two.", "cab_coffee Three."]),
    );
  });

  test("ready reads the chat again, with a read that begins after the stream opened", async () => {
    const api = mockApi({ [CHAT]: json(200, chat()) });
    renderPanel({}, live);

    await screen.findByText(FIRST);
    expect(api.calls(CHAT)).toHaveLength(1);

    api.on(CHAT, json(200, chat({ items: [...chat().items, stored] })));
    act(() => streams.latest().send(readyEvent()));

    expect(await screen.findByText(TYPED, { exact: false })).toBeInTheDocument();
    // The topic's read and the event's are one read.
    expect(api.calls(CHAT)).toHaveLength(2);
  });

  test("a first read that is on its way at ready answers, and the chat is read once more", async () => {
    const first = deferred();
    const api = mockApi({ [CHAT]: first.handler });
    renderPanel({}, live);

    act(() => streams.latest().send(readyEvent()));
    await waitFor(() => expect(api.calls(CHAT)).toHaveLength(1));
    // The first read saw the chat before the stream opened; a message came in between.
    api.on(CHAT, json(200, chat({ items: [...chat().items, stored] })));
    first.resolve(json(200, chat()));

    expect(await screen.findByText(TYPED, { exact: false })).toBeInTheDocument();
    expect(api.calls(CHAT)).toHaveLength(2);
  });

  test("broadcast.changed of the market reads the chat again; one of another market does not", async () => {
    const api = mockApi({ [CHAT]: json(200, chat()) });
    renderPanel({}, live);

    await screen.findByText(FIRST);
    act(() => streams.latest().send(broadcastChangedEvent({ market_id: OTHER_MARKET_ID })));
    // Several announcements of the market are one read.
    api.on(CHAT, json(200, closedChat()));
    act(() => {
      streams.latest().send(broadcastChangedEvent());
      streams.latest().send(broadcastChangedEvent());
    });

    expect(await screen.findByText("The live chat is closed.")).toBeInTheDocument();
    expect(api.calls(CHAT)).toHaveLength(2);
    expect(screen.queryByRole("textbox", { name: "Message" })).not.toBeInTheDocument();
  });

  test("an announcement waits out the spread of /config before its read", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      vi.mocked(Math.random).mockReturnValue(0.5);
      const api = mockApi({ [CHAT]: json(200, chat()) });
      renderPanel({}, { live: true, config: config({ live_announce_spread_ms: 60_000 }) });

      await screen.findByText(FIRST);
      act(() => streams.latest().send(broadcastChangedEvent()));
      await act(() => vi.advanceTimersByTimeAsync(29_000));
      expect(api.calls(CHAT)).toHaveLength(1);

      await act(() => vi.advanceTimersByTimeAsync(1_500));
      await waitFor(() => expect(api.calls(CHAT)).toHaveLength(2));
    } finally {
      vi.useRealTimers();
    }
  });

  test("without a stream the chat is read at every poll", async () => {
    vi.mocked(Math.random).mockReturnValue(0);
    const api = mockApi({ [CHAT]: json(200, chat()) });
    renderPanel({}, live);

    await screen.findByText(FIRST);
    api.on(CHAT, json(200, chat({ items: [...chat().items, stored] })));
    act(() => streams.latest().fail());

    expect(await screen.findByText(TYPED, { exact: false })).toBeInTheDocument();
    expect(api.calls(CHAT)).toHaveLength(2);
  });

  test("the chat is read again when the window gets the focus back", async () => {
    const api = mockApi({ [CHAT]: json(200, chat()) });
    renderPanel();

    await screen.findByText(FIRST);
    act(() => {
      window.dispatchEvent(new Event("visibilitychange"));
    });

    await waitFor(() => expect(api.calls(CHAT)).toHaveLength(2));
  });

  describe("a read replaces the list, and what arrived while it was on its way is put again", () => {
    /** The panel on screen, the stream open, and a second read of the chat that waits. */
    async function duringARead() {
      const second = deferred();
      const api = mockApi({ [CHAT]: json(200, chat()) });
      const rendered = renderPanel({}, live);
      await screen.findByText(FIRST);
      api.on(CHAT, second.handler);
      act(() => streams.latest().send(readyEvent()));
      await waitFor(() => expect(api.calls(CHAT)).toHaveLength(2));
      return { ...rendered, api, second };
    }

    test("a message that arrived during the read is still there, after what the read returned", async () => {
      const { second, client } = await duringARead();

      act(() => streams.latest().send(chatMessageEvent()));
      expect(await screen.findByText(TYPED, { exact: false })).toBeInTheDocument();

      // The read saw the chat before that message, and another one the tab never got.
      const missed = chatMessage({ id: "0c0c0c0c-1111-4222-8333-444444444444", text: "Missed." });
      second.resolve(json(200, chat({ items: [...chat().items, missed] })));

      await waitFor(() =>
        expect(texts()).toEqual([
          `cab_coffee ${FIRST}`,
          `vis_moritz VIS ${SECOND}`,
          "cab_coffee Missed.",
          `anna_m ${TYPED}`,
        ]),
      );
      expect(client.getQueryData<ChatResponse>(keys.markets.chat(MARKET_ID))?.items).toHaveLength(
        4,
      );
    });

    test("a message the read returned and the event brought stands once", async () => {
      const { second } = await duringARead();

      act(() => streams.latest().send(chatMessageEvent({ message: stored })));
      await screen.findByText(TYPED, { exact: false });
      second.resolve(json(200, chat({ items: [...chat().items, stored] })));

      await waitFor(() => expect(rows()).toHaveLength(3));
      expect(screen.getAllByText(TYPED, { exact: false })).toHaveLength(1);
    });

    test("a removal that arrived during the read holds against a read that still saw the message", async () => {
      const { second } = await duringARead();

      act(() => streams.latest().send(chatRemovedEvent()));
      await waitFor(() => expect(rows()).toHaveLength(1));
      second.resolve(json(200, chat()));

      // The answer is taken, and the removed row does not come back with it.
      await waitFor(() => expect(sendButton()).toBeInTheDocument());
      await act(() => new Promise((resolve) => setTimeout(resolve, 20)));
      expect(texts()).toEqual([`vis_moritz VIS ${SECOND}`]);
    });

    test("the viewer's own message is kept too when a read answers without it", async () => {
      const { second, api, user } = await duringARead();

      api.on(POST, json(201, stored));
      await user.type(field(), `${TYPED}{Enter}`);
      await waitFor(() => expect(field()).toHaveValue(""));
      second.resolve(json(200, chat()));

      await act(() => new Promise((resolve) => setTimeout(resolve, 20)));
      expect(texts()).toEqual([
        `cab_coffee ${FIRST}`,
        `vis_moritz VIS ${SECOND}`,
        `anna_m ${TYPED}`,
      ]);
    });

    test("a read that fails forgets what arrived during it: the next read has seen it", async () => {
      const { second, api } = await duringARead();

      act(() => streams.latest().send(chatMessageEvent({ message: stored })));
      await screen.findByText(TYPED, { exact: false });
      second.resolve(apiError(500, "INTERNAL_ERROR", "Something broke."));
      await waitFor(() => expect(api.calls(CHAT)).toHaveLength(2));
      await act(() => new Promise((resolve) => setTimeout(resolve, 20)));
      // The list is as it was, with what arrived.
      expect(rows()).toHaveLength(3);

      // An admin removes that message while no read is on its way, and the next read says so.
      act(() => streams.latest().send(chatRemovedEvent({ message_id: stored.id })));
      await waitFor(() => expect(rows()).toHaveLength(2));
      api.on(CHAT, json(200, chat()));
      act(() => {
        window.dispatchEvent(new Event("visibilitychange"));
      });

      await waitFor(() => expect(api.calls(CHAT)).toHaveLength(3));
      await act(() => new Promise((resolve) => setTimeout(resolve, 20)));
      expect(texts()).toEqual([`cab_coffee ${FIRST}`, `vis_moritz VIS ${SECOND}`]);
    });

    test("what arrived before a read began is not put again: the read has seen it", async () => {
      const api = mockApi({ [CHAT]: json(200, chat()) });
      renderPanel({}, live);
      await screen.findByText(FIRST);

      act(() => streams.latest().send(chatMessageEvent()));
      await screen.findByText(TYPED, { exact: false });
      // An admin removed it meanwhile, and the removal never reached this tab.
      api.on(CHAT, json(200, chat()));
      act(() => streams.latest().send(readyEvent()));

      await waitFor(() => expect(api.calls(CHAT)).toHaveLength(2));
      await waitFor(() => expect(rows()).toHaveLength(2));
      expect(screen.queryByText(TYPED, { exact: false })).not.toBeInTheDocument();
    });
  });
});

describe("a chat belongs to one live stream", () => {
  const earlier = () =>
    chat({
      broadcast_id: OTHER_BROADCAST_ID,
      items: [chatMessage({ id: OTHER_CHAT_MESSAGE_ID, text: "An earlier stream." })],
    });

  test("the cached chat of an earlier live stream is not shown: the panel loads until its read answers", async () => {
    const answer = deferred();
    mockApi({ [CHAT]: answer.handler });
    const client = createTestQueryClient();
    client.setQueryData(keys.markets.chat(MARKET_ID), earlier());
    renderPanel({}, { client });

    expect(panel()).toHaveAttribute("data-state", "loading");
    expect(screen.queryByText("An earlier stream.", { exact: false })).not.toBeInTheDocument();
    expect(field()).toBeDisabled();

    answer.resolve(json(200, chat({ items: [] })));
    expect(await screen.findByText("No messages yet.")).toBeInTheDocument();
  });

  test("the closed chat the cache holds between two live streams is not shown either", async () => {
    const answer = deferred();
    mockApi({ [CHAT]: answer.handler });
    const client = createTestQueryClient();
    client.setQueryData(keys.markets.chat(MARKET_ID), closedChat());
    renderPanel({}, { client });

    expect(panel()).toHaveAttribute("data-state", "loading");
    expect(screen.queryByText("The live chat is closed.")).not.toBeInTheDocument();

    answer.resolve(json(200, chat()));
    expect(await screen.findByText(FIRST)).toBeInTheDocument();
  });

  test("a closed chat stays closed while it is read again", async () => {
    const again = deferred();
    const api = mockApi({ [CHAT]: json(200, closedChat()) });
    renderPanel();

    await screen.findByText("The live chat is closed.");
    api.on(CHAT, again.handler);
    act(() => {
      window.dispatchEvent(new Event("visibilitychange"));
    });

    await waitFor(() => expect(api.calls(CHAT)).toHaveLength(2));
    expect(panel()).toHaveAttribute("data-state", "closed");
    again.resolve(json(200, closedChat()));
  });

  test("a read that answers another live stream than the one on screen shows no row of it", async () => {
    mockApi({ [CHAT]: json(200, earlier()) });
    const { client } = renderPanel();

    await waitFor(() =>
      expect(client.getQueryData(keys.markets.chat(MARKET_ID))).toEqual(earlier()),
    );
    expect(panel()).toHaveAttribute("data-state", "loading");
    expect(screen.queryByText("An earlier stream.", { exact: false })).not.toBeInTheDocument();
  });

  test("the rows on screen stay when reading them again fails", async () => {
    const api = mockApi({ [CHAT]: json(200, chat()) });
    renderPanel();

    await screen.findByText(FIRST);
    api.on(CHAT, apiError(500, "INTERNAL_ERROR", "Something broke."));
    act(() => {
      window.dispatchEvent(new Event("visibilitychange"));
    });

    await waitFor(() => expect(api.calls(CHAT)).toHaveLength(2));
    await act(() => new Promise((resolve) => setTimeout(resolve, 20)));
    expect(rows()).toHaveLength(2);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  test("when another live stream begins, what was typed for the earlier one is let go of", async () => {
    const api = mockApi({ [CHAT]: json(200, chat()) });
    const { user, rerender } = renderPanel();

    await screen.findByText(FIRST);
    await user.type(field(), "Half a sen");
    api.on(CHAT, json(200, chat({ broadcast_id: OTHER_BROADCAST_ID, items: [] })));
    rerender(<ChatPanel marketId={MARKET_ID} broadcastId={OTHER_BROADCAST_ID} />);

    expect(await screen.findByText("No messages yet.")).toBeInTheDocument();
    expect(field()).toHaveValue("");
    expect(screen.queryByText(FIRST)).not.toBeInTheDocument();
  });
});

describe("the end of the log", () => {
  const end = () => panel().querySelector('[data-slot="chat-log-end"]')!;
  /** jsdom lays nothing out: the test says how tall the content of the log is. */
  const tall = (log: HTMLElement, height: number) =>
    Object.defineProperty(log, "scrollHeight", { configurable: true, value: height });

  test("the log stays at its end while the reader is there: a message that arrives is in sight", async () => {
    const streams = installFakeEventSource();
    mockApi({ [CHAT]: json(200, chat()) });
    renderPanel({}, { live: true });

    await screen.findByText(FIRST);
    const log = screen.getByRole("log");
    tall(log, 900);
    act(() => streams.latest().send(chatMessageEvent()));

    await screen.findByText(TYPED, { exact: false });
    expect(log.scrollTop).toBe(900);
    expect(screen.queryByRole("button", { name: "Latest messages" })).not.toBeInTheDocument();
  });

  test("Latest messages shows once the end is out of sight, and takes the log to its end", async () => {
    const scrolling = watchScrolling();
    const streams = installFakeEventSource();
    mockApi({ [CHAT]: json(200, chat()) });
    const { user } = renderPanel({}, { live: true });

    await screen.findByText(FIRST);
    const log = screen.getByRole("log");
    expect(screen.queryByRole("button", { name: "Latest messages" })).not.toBeInTheDocument();

    // The reader scrolls up.
    tall(log, 900);
    log.scrollTop = 120;
    act(() => scrolling.notReached(end()));
    const latest = await screen.findByRole("button", { name: "Latest messages" });
    // It stands under the rows, in a row of its own: nothing lies over a message.
    expect(log).not.toContainElement(latest);

    // What arrives meanwhile does not move the view.
    act(() => streams.latest().send(chatMessageEvent()));
    await screen.findByText(TYPED, { exact: false });
    expect(log.scrollTop).toBe(120);

    await user.click(latest);

    expect(log.scrollTop).toBe(900);
    expect(log).toHaveFocus();
    // The browser sees the end in sight, and the button goes.
    act(() => scrolling.inView(end()));
    expect(screen.queryByRole("button", { name: "Latest messages" })).not.toBeInTheDocument();

    // From here on what arrives is in sight again.
    tall(log, 960);
    act(() =>
      streams.latest().send(
        chatMessageEvent({
          message: chatMessage({
            id: "0d0d0d0d-1111-4222-8333-444444444444",
            text: "And another.",
          }),
        }),
      ),
    );
    await screen.findByText("And another.", { exact: false });
    expect(log.scrollTop).toBe(960);
  });

  test("the button goes when the reader scrolls back to the end", async () => {
    const scrolling = watchScrolling();
    mockApi({ [CHAT]: json(200, chat()) });
    renderPanel();

    await screen.findByText(FIRST);
    act(() => scrolling.notReached(end()));
    await screen.findByRole("button", { name: "Latest messages" });
    act(() => scrolling.inView(end()));

    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Latest messages" })).not.toBeInTheDocument(),
    );
  });
});

describe("what the host says", () => {
  test("the panel has its own height, and takes the height its column leaves when told to", async () => {
    mockApi({ [CHAT]: json(200, chat()) });
    const { rerender } = renderPanel();

    await screen.findByText(FIRST);
    expect(panel()).toHaveClass("h-80", "md:h-90", "overflow-hidden");

    rerender(<ChatPanel marketId={MARKET_ID} broadcastId={BROADCAST_ID} fill className="mt-2" />);

    expect(panel()).toHaveClass("min-h-50", "flex-1", "md:h-90", "mt-2");
    expect(panel()).not.toHaveClass("h-80");
    // The log takes what header and composer leave, and scrolls inside the panel.
    expect(screen.getByRole("log")).toHaveClass(
      "min-h-0",
      "flex-1",
      "overflow-y-auto",
      "overscroll-contain",
    );
  });

  test("the host hears when the field takes the focus and when it gives it away", async () => {
    const onComposerFocus = vi.fn();
    mockApi({ [CHAT]: json(200, chat()) });
    const { user } = renderPanel({ onComposerFocus });

    await screen.findByText(FIRST);
    await user.click(field());
    expect(onComposerFocus.mock.calls).toEqual([[true]]);

    await user.tab({ shift: true });
    expect(onComposerFocus.mock.calls).toEqual([[true], [false]]);
  });

  test("a composer that leaves the panel with the focus in its field says so", async () => {
    const onComposerFocus = vi.fn();
    mockApi({
      [CHAT]: json(200, chat()),
      [POST]: apiError(409, "CONFLICT", "The live chat is closed."),
    });
    const { user } = renderPanel({ onComposerFocus });

    await screen.findByText(FIRST);
    await user.type(field(), `${TYPED}{Enter}`);

    await waitFor(() => expect(panel()).toHaveAttribute("data-state", "closed"));
    expect(onComposerFocus.mock.calls).toEqual([[true], [false]]);
  });
});
