import { act, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";

import { BATCH_ID, codeBatch, codeBatchCreated, config, me } from "@/test/fixtures";
import { apiError, deferred, json, mockApi, noAnswer, type Route } from "@/test/http";
import { navigation } from "@/test/navigation";
import { renderWithProviders } from "@/test/render";

import { AdminCodesScreen } from "./admin-codes-screen";
import { browser } from "./browser";

/* What a QR holds cannot be read off its picture: the test looks at what it was given. */
vi.mock("qrcode.react", () => ({
  QRCodeSVG: ({ value, "aria-label": label }: { value: string; "aria-label"?: string }) => (
    <svg role="img" aria-label={label} data-value={value} />
  ),
}));

const BATCHES: Route = "GET /api/admin/code-batches";
const CREATE: Route = "POST /api/admin/code-batches";
const REVOKE: Route = "POST /api/admin/code-batches/{batch_id}/revoke";

const SAVE_NOW = "These codes cannot be shown again. Save them now.";
const UNANSWERED =
  "We did not get an answer. If the batch is in the table below, revoke it and create it again: its codes cannot be shown.";

const admin = { me: me({ role: "ADMIN" }) };
type User = ReturnType<typeof renderWithProviders>["user"];

const created = () =>
  codeBatchCreated({
    batch: codeBatch({
      id: "new-batch",
      label: "Katerbrunch",
      value_cr: "12.5",
      count: 2,
      active_count: 2,
      redeemed_count: 0,
      total_value_cr: "25",
      expires_at: "2026-12-31T22:59:00Z",
    }),
  });

function show(routes: Partial<Record<Route, Response>> = {}) {
  const api = mockApi({ [BATCHES]: json(200, { items: [codeBatch()] }), ...routes });
  const rendered = renderWithProviders(<AdminCodesScreen />, admin);
  return { api, ...rendered };
}

/** What lets go of a file that was offered for saving. */
const releaseFile = vi.fn();

const newBatch = () => screen.getByRole("button", { name: "New batch" });
/** The codes as the screen shows them, whether or not a dialog stands in front of them. */
const shownCodes = () => document.querySelector("[data-slot=created-codes]")?.textContent ?? "";

async function openForm(user: User) {
  await screen.findByRole("table", { name: "Batches" });
  await user.click(newBatch());
  return screen.findByRole("dialog", { name: "New batch" });
}

async function fillIn(user: User, dialog: HTMLElement) {
  await user.type(within(dialog).getByLabelText("Label"), "Katerbrunch");
  await user.type(within(dialog).getByLabelText("Number of codes"), "2");
  await user.type(within(dialog).getByLabelText("Expires"), "2026-12-31T23:59");
}

/** Creates a batch and waits until its codes are on screen. */
async function create(user: User) {
  const dialog = await openForm(user);
  await fillIn(user, dialog);
  await user.click(within(dialog).getByRole("button", { name: "Create batch" }));
  return screen.findByRole("region", { name: "Katerbrunch" });
}

beforeEach(() => {
  releaseFile.mockClear();
  navigation.go("/admin/codes");
  vi.spyOn(browser, "saveFile").mockImplementation(() => releaseFile);
  vi.spyOn(browser, "print").mockImplementation(() => {});
  // The history is watched, not walked; a step back that was asked for arrives a moment later.
  vi.spyOn(window.history, "pushState").mockImplementation(() => {});
  vi.spyOn(window.history, "back").mockImplementation(() => {
    queueMicrotask(() => window.dispatchEvent(new PopStateEvent("popstate")));
  });
});

describe("Admin: code batches", () => {
  test("lists the batches with what became of their codes, as the API sent it", async () => {
    show({
      [BATCHES]: json(200, {
        items: [
          codeBatch({
            label: "Welcome week",
            value_cr: "35",
            count: 1200,
            active_count: 1014,
            redeemed_count: 180,
            revoked_count: 6,
            expires_at: "2026-12-31T22:59:00Z",
          }),
        ],
      }),
    });

    const table = await screen.findByRole("table", { name: "Batches" });
    expect(
      within(table)
        .getAllByRole("columnheader")
        .map((header) => header.textContent),
    ).toEqual([
      "Label",
      "Tokens per code",
      "Codes",
      "Active",
      "Redeemed",
      "Revoked",
      "Expires",
      "Actions",
    ]);
    const [, row] = within(table).getAllByRole("row");
    expect(
      within(row!)
        .getAllByRole("cell")
        .slice(0, 7)
        .map((cell) => cell.textContent),
    ).toEqual(["Welcome week", "3535 tokens", "1,200", "1,014", "180", "6", "31 Dec 2026, 23:59"]);
    expect(within(row!).getByRole("button", { name: "Revoke Welcome week" })).toBeEnabled();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Codes");
    expect(screen.getByRole("link", { name: "Codes" })).toHaveAttribute("aria-current", "page");
  });

  test("a batch that was revoked as a whole says so in place of the button", async () => {
    show({
      [BATCHES]: json(200, {
        items: [
          codeBatch({ label: "Revoked batch", revoked_at: "2026-10-02T08:00:00Z" }),
          codeBatch({ id: "other", label: "Running batch" }),
        ],
      }),
    });

    const table = await screen.findByRole("table", { name: "Batches" });
    const [, revoked, running] = within(table).getAllByRole("row");
    expect(within(revoked!).queryByRole("button")).not.toBeInTheDocument();
    expect(within(revoked!).getAllByRole("cell").at(-1)).toHaveTextContent("Revoked");
    expect(within(running!).getByRole("button", { name: "Revoke Running batch" })).toBeVisible();
  });

  test("shows skeleton rows while the batches load", async () => {
    const answer = deferred();
    mockApi({ [BATCHES]: answer.handler });
    renderWithProviders(<AdminCodesScreen />, admin);

    expect(document.querySelector("[data-slot=table-skeleton]")).toBeInTheDocument();
    expect(screen.queryByText("No batches yet.")).not.toBeInTheDocument();

    answer.resolve(json(200, { items: [] }));
    expect(await screen.findByText("No batches yet.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  test("batches that cannot be read show the API's message with Try again", async () => {
    const api = mockApi({ [BATCHES]: apiError(500, "INTERNAL_ERROR", "Something went wrong.") });
    const { user } = renderWithProviders(<AdminCodesScreen />, admin);

    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong.");

    api.on(BATCHES, json(200, { items: [codeBatch()] }));
    await user.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findByRole("table", { name: "Batches" })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("New batch", () => {
  test("asks for the label, the number of codes, what each is for, the expiry and one code per person", async () => {
    const { user } = show();
    const dialog = await openForm(user);

    expect(within(dialog).getByLabelText("Label")).toHaveValue("");
    expect(within(dialog).getByLabelText("Number of codes")).toHaveValue("");
    expect(within(dialog).getByLabelText("Expires")).toHaveValue("");
    expect(within(dialog).getByRole("checkbox", { name: "One code per person" })).toBeChecked();
    expect(within(dialog).getByRole("button", { name: "Create batch" })).toBeDisabled();
  });

  test("what a code is for is filled in with the default of /config, between the bounds of /config", async () => {
    mockApi({ [BATCHES]: json(200, { items: [codeBatch()] }) });
    const { user } = renderWithProviders(<AdminCodesScreen />, {
      ...admin,
      config: config({
        code_default_value_cr: "40",
        code_min_value_cr: "2.5",
        code_max_value_cr: "1250",
      }),
    });
    const dialog = await openForm(user);

    const value = within(dialog).getByLabelText("Tokens per code");
    expect(value).toHaveValue("40");
    expect(value).toHaveAccessibleDescription("Between 2.5 and 1,250 tokens");
  });

  test("the button waits until everything is filled in, and a number of codes is digits", async () => {
    const { user } = show();
    const dialog = await openForm(user);
    const go = within(dialog).getByRole("button", { name: "Create batch" });

    await user.type(within(dialog).getByLabelText("Label"), "Katerbrunch");
    await user.type(within(dialog).getByLabelText("Expires"), "2026-12-31T23:59");
    expect(go).toBeDisabled();

    await user.type(within(dialog).getByLabelText("Number of codes"), "2o");
    expect(go).toBeDisabled();
    await user.type(within(dialog).getByLabelText("Number of codes"), "{Backspace}");
    expect(go).toBeEnabled();

    await user.clear(within(dialog).getByLabelText("Tokens per code"));
    expect(go).toBeDisabled();
  });

  test("sends the count as a number, the value as the string that was typed and the expiry with the zone's offset", async () => {
    const { api, user } = show({ [CREATE]: json(201, created()) });
    const dialog = await openForm(user);

    await fillIn(user, dialog);
    await user.clear(within(dialog).getByLabelText("Tokens per code"));
    await user.type(within(dialog).getByLabelText("Tokens per code"), " 12.50 ");
    await user.click(within(dialog).getByRole("checkbox", { name: "One code per person" }));
    await user.click(within(dialog).getByRole("button", { name: "Create batch" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    // Never a number made from the value: "12.50" would have become 12.5.
    expect(api.lastCall(CREATE)?.rawBody).toBe(
      '{"label":"Katerbrunch","count":2,"value_cr":"12.50","expires_at":"2026-12-31T23:59:00+01:00","one_per_user":false}',
    );
  });

  test("the bounds are for the reader: a value outside them is sent, and the API's refusal stands on its field", async () => {
    const { api, user } = show({
      [CREATE]: apiError(422, "VALIDATION_ERROR", "The request is not valid.", {
        field: "value_cr",
      }),
    });
    const dialog = await openForm(user);

    await fillIn(user, dialog);
    await user.clear(within(dialog).getByLabelText("Tokens per code"));
    await user.type(within(dialog).getByLabelText("Tokens per code"), "100000");
    await user.click(within(dialog).getByRole("button", { name: "Create batch" }));

    await waitFor(() => expect(within(dialog).getByLabelText("Tokens per code")).toBeInvalid());
    expect(api.lastCall(CREATE)?.body).toMatchObject({ value_cr: "100000" });
    expect(within(dialog).getByLabelText("Tokens per code")).toHaveAccessibleDescription(
      /The request is not valid\./,
    );
    expect(within(dialog).getByLabelText("Label")).toHaveValue("Katerbrunch");
    expect(screen.queryByText(SAVE_NOW)).not.toBeInTheDocument();
  });

  test.each([
    ["count", "Number of codes", "Input should be less than or equal to 10000"],
    ["expires_at", "Expires", "The request is not valid."],
    ["label", "Label", "String should have at most 100 characters"],
  ])("a refusal of %s stands on its field", async (field, label, message) => {
    const { user } = show({
      [CREATE]: apiError(422, "VALIDATION_ERROR", "The request is not valid.", {
        errors: [{ loc: ["body", field], msg: message, type: "x" }],
      }),
    });
    const dialog = await openForm(user);

    await fillIn(user, dialog);
    await user.click(within(dialog).getByRole("button", { name: "Create batch" }));

    await waitFor(() => expect(within(dialog).getByLabelText(label)).toBeInvalid());
    expect(within(dialog).getByLabelText(label)).toHaveAccessibleDescription(
      new RegExp(message.replaceAll(".", "\\.")),
    );
  });

  test("LIMIT_EXCEEDED shows the API's message in the dialog, which keeps its input", async () => {
    const { user } = show({
      [CREATE]: apiError(409, "LIMIT_EXCEEDED", "This would exceed a limit.", {
        limit: "MAX_CODE_ISSUANCE_PER_MONTH",
        issued_cr: "6990",
        requested_cr: "25",
        cap_cr: "7000",
      }),
    });
    const dialog = await openForm(user);

    await fillIn(user, dialog);
    await user.click(within(dialog).getByRole("button", { name: "Create batch" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "This would exceed a limit.",
    );
    expect(screen.getByRole("dialog", { name: "New batch" })).toBeInTheDocument();
    expect(within(dialog).getByLabelText("Label")).toHaveValue("Katerbrunch");
    expect(within(dialog).getByLabelText("Label")).not.toBeInvalid();

    await user.type(within(dialog).getByLabelText("Number of codes"), "0");
    expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();
  });

  test("the button is disabled from tap until the answer: two taps are one batch", async () => {
    const answer = deferred();
    const api = mockApi({ [BATCHES]: json(200, { items: [] }), [CREATE]: answer.handler });
    const { user } = renderWithProviders(<AdminCodesScreen />, admin);
    await screen.findByText("No batches yet.");
    await user.click(newBatch());
    const dialog = await screen.findByRole("dialog", { name: "New batch" });

    await fillIn(user, dialog);
    await user.click(within(dialog).getByRole("button", { name: "Create batch" }));
    await user.click(within(dialog).getByRole("button", { name: "Create batch" }));
    await user.keyboard("{Enter}");

    expect(within(dialog).getByRole("button", { name: "Create batch" })).toHaveAttribute(
      "aria-busy",
      "true",
    );
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toBeDisabled();
    expect(api.calls(CREATE)).toHaveLength(1);

    answer.resolve(json(201, created()));
    expect(await screen.findByText(SAVE_NOW)).toBeInTheDocument();
    expect(api.calls(CREATE)).toHaveLength(1);
  });

  test("without an answer the table is read again before anything else, and the screen says what to do with a batch that is in it", async () => {
    const reread = deferred();
    const { api, user } = show();
    api.on(CREATE, noAnswer);
    const dialog = await openForm(user);
    api.on(BATCHES, reread.handler);

    await fillIn(user, dialog);
    await user.click(within(dialog).getByRole("button", { name: "Create batch" }));

    await waitFor(() => expect(api.calls(BATCHES)).toHaveLength(2));
    expect(within(dialog).getByRole("button", { name: "Create batch" })).toHaveAttribute(
      "aria-busy",
      "true",
    );
    expect(screen.queryByText(UNANSWERED)).not.toBeInTheDocument();

    // The batch did arrive: it is in the table, and its codes cannot be shown.
    reread.resolve(json(200, { items: [created().batch, codeBatch()] }));

    expect(await screen.findByText(UNANSWERED)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(
      within(screen.getByRole("table", { name: "Batches" })).getByText("Katerbrunch"),
    ).toBeInTheDocument();
    expect(screen.queryByText(SAVE_NOW)).not.toBeInTheDocument();
    expect(api.calls(CREATE)).toHaveLength(1);
    expect(newBatch()).toBeEnabled();

    // Trying again starts from what was typed.
    await user.click(newBatch());
    const again = await screen.findByRole("dialog", { name: "New batch" });
    expect(within(again).getByLabelText("Label")).toHaveValue("Katerbrunch");
    expect(within(again).queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByText(UNANSWERED)).not.toBeInTheDocument();
  });
});

describe("the codes of a new batch are shown once", () => {
  test("under the banner, with the batch they belong to, and no other batch can be made meanwhile", async () => {
    const { user } = show({ [CREATE]: json(201, created()) });

    const codes = await create(user);

    expect(within(codes).getByText(SAVE_NOW)).toBeInTheDocument();
    expect(within(codes).getByRole("heading", { level: 2, name: "Katerbrunch" })).toBeVisible();
    expect(within(codes).getByText("2 codes")).toBeInTheDocument();
    expect(
      within(within(codes).getByRole("list", { name: "Codes" }))
        .getAllByRole("listitem")
        .map((item) => item.textContent),
    ).toEqual(["4F7K-Q2M9-XAB3", "T8WN-3CB4-Z6PD"]);
    // The banner stands above the codes and their two buttons.
    const banner = within(codes).getByText(SAVE_NOW);
    for (const below of [
      within(codes).getByRole("button", { name: "Download CSV" }),
      within(codes).getByRole("button", { name: "Print QR sheet" }),
      within(codes).getByRole("list", { name: "Codes" }),
    ]) {
      expect(banner.compareDocumentPosition(below) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(0);
    }
    expect(newBatch()).toBeDisabled();
    // The dialog is gone and its button cannot be used: the focus is on what it left behind.
    await waitFor(() =>
      expect(within(codes).getByRole("heading", { level: 2, name: "Katerbrunch" })).toHaveFocus(),
    );
  });

  test("Download CSV makes the file in the browser from the answer", async () => {
    const { api, user } = show({ [CREATE]: json(201, created()) });
    const codes = await create(user);
    const requests = api.requests.length;

    await user.click(within(codes).getByRole("button", { name: "Download CSV" }));

    expect(browser.saveFile).toHaveBeenCalledTimes(1);
    expect(browser.saveFile).toHaveBeenCalledWith(
      "codes-katerbrunch.csv",
      [
        "code,redeem_url,label,tokens,expires_at",
        "4F7KQ2M9XAB3,http://localhost:8080/wallet?code=4F7KQ2M9XAB3,Katerbrunch,12.5,2026-12-31T22:59:00Z",
        "T8WN3CB4Z6PD,http://localhost:8080/wallet?code=T8WN3CB4Z6PD,Katerbrunch,12.5,2026-12-31T22:59:00Z",
        "",
      ].join("\r\n"),
      "text/csv;charset=utf-8",
    );
    // Nothing was asked of the API for it.
    expect(api.requests).toHaveLength(requests);
    // The file stays readable while the codes are on screen.
    expect(releaseFile).not.toHaveBeenCalled();
  });

  test("a file that was made for a download is let go of together with the codes", async () => {
    const { user } = show({ [CREATE]: json(201, created()) });
    const codes = await create(user);
    await user.click(within(codes).getByRole("button", { name: "Download CSV" }));
    await user.click(within(codes).getByRole("button", { name: "Download CSV" }));

    await user.click(within(codes).getByRole("button", { name: "Done" }));
    await user.click(
      within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Close" }),
    );

    await waitFor(() => expect(screen.queryByText(SAVE_NOW)).not.toBeInTheDocument());
    expect(releaseFile).toHaveBeenCalledTimes(2);
  });

  test("Print QR sheet lays the sheet out and prints it: its QR codes hold the redeem link of their code, under the dark logo", async () => {
    const { api, user } = show({ [CREATE]: json(201, created()) });
    const codes = await create(user);
    const requests = api.requests.length;
    const sheet = document.querySelector<HTMLElement>("[data-slot=print-sheet]")!;
    const cards = () => within(sheet).queryAllByRole("listitem");

    // Until it is asked for the sheet holds its logo and no card, and paper shows the page.
    expect(cards()).toHaveLength(0);
    expect(sheet).toHaveClass("hidden", "text-ink");
    expect(sheet).not.toHaveClass("print:block");
    expect(codes.closest(".print\\:hidden")).toBeNull();
    expect(within(sheet).getByRole("img", { name: "MarkETH" })).toHaveAttribute(
      "src",
      expect.stringContaining("logo-lockup-dark.png"),
    );
    expect(browser.print).not.toHaveBeenCalled();

    // The print dialog opens once the cards are on the page.
    let cardsAtPrint = -1;
    vi.mocked(browser.print).mockImplementation(() => {
      cardsAtPrint = cards().length;
    });
    await user.click(within(codes).getByRole("button", { name: "Print QR sheet" }));

    await waitFor(() => expect(browser.print).toHaveBeenCalledTimes(1));
    expect(cardsAtPrint).toBe(2);
    expect(api.requests).toHaveLength(requests);
    // Now paper shows the sheet and nothing else; the screen shows everything but the sheet.
    expect(sheet).toHaveClass("hidden", "print:block");
    expect(codes.closest(".print\\:hidden")).not.toBeNull();
    expect(sheet.closest(".print\\:hidden")).toBeNull();
    expect(
      cards().map((card) => [
        card.querySelector("svg")?.getAttribute("data-value"),
        card.querySelector("[data-slot=code]")?.textContent,
        card.querySelector("[data-slot=amount] .sr-only")?.textContent,
      ]),
    ).toEqual([
      ["http://localhost:8080/wallet?code=4F7KQ2M9XAB3", "4F7K-Q2M9-XAB3", "12.5 tokens"],
      ["http://localhost:8080/wallet?code=T8WN3CB4Z6PD", "T8WN-3CB4-Z6PD", "12.5 tokens"],
    ]);

    // Asked for again, it is printed again.
    await user.click(within(codes).getByRole("button", { name: "Print QR sheet" }));
    await waitFor(() => expect(browser.print).toHaveBeenCalledTimes(2));
  });

  test("printing through the browser's own menu prints the sheet too", async () => {
    const { user } = show({ [CREATE]: json(201, created()) });
    const codes = await create(user);
    const sheet = document.querySelector<HTMLElement>("[data-slot=print-sheet]")!;
    expect(within(sheet).queryAllByRole("listitem")).toHaveLength(0);

    // The browser says it is about to print, and looks at the page right after.
    act(() => {
      window.dispatchEvent(new Event("beforeprint"));
    });

    expect(within(sheet).getAllByRole("listitem")).toHaveLength(2);
    expect(sheet).toHaveClass("print:block");
    expect(codes.closest(".print\\:hidden")).not.toBeNull();
    // The browser is printing already: the page does not ask it to.
    expect(browser.print).not.toHaveBeenCalled();
  });

  test("there is no sheet while there are no codes", async () => {
    show();
    await screen.findByRole("table", { name: "Batches" });

    expect(document.querySelector("[data-slot=print-sheet]")).toBeNull();
    expect(screen.queryByText(SAVE_NOW)).not.toBeInTheDocument();
  });

  test("Done asks before the codes go, and Cancel keeps them", async () => {
    const { user } = show({ [CREATE]: json(201, created()) });
    const codes = await create(user);

    await user.click(within(codes).getByRole("button", { name: "Done" }));

    const question = await screen.findByRole("alertdialog", { name: "Close the codes?" });
    expect(question).toHaveAccessibleDescription("These codes cannot be shown again.");
    await user.click(within(question).getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(shownCodes()).toContain("4F7K-Q2M9-XAB3");
    expect(screen.getByText(SAVE_NOW)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "Done" })).toHaveFocus());
  });

  test("once Done is confirmed the codes are gone for good, and a batch can be made again", async () => {
    const { user } = show({ [CREATE]: json(201, created()) });
    const codes = await create(user);

    await user.click(within(codes).getByRole("button", { name: "Done" }));
    const question = await screen.findByRole("alertdialog", { name: "Close the codes?" });
    const close = within(question).getByRole("button", { name: "Close" });
    expect(close).toHaveAttribute("data-variant", "destructive");
    await user.click(close);

    await waitFor(() => expect(screen.queryByText(SAVE_NOW)).not.toBeInTheDocument());
    expect(screen.queryByText(/4F7K/)).not.toBeInTheDocument();
    expect(document.querySelector("[data-slot=print-sheet]")).toBeNull();
    expect(document.body.innerHTML).not.toContain("4F7KQ2M9XAB3");
    expect(newBatch()).toBeEnabled();
    await waitFor(() =>
      expect(screen.getByRole("heading", { level: 2, name: "Batches" })).toHaveFocus(),
    );

    // The form starts afresh for the next batch.
    await user.click(newBatch());
    const dialog = await screen.findByRole("dialog", { name: "New batch" });
    expect(within(dialog).getByLabelText("Label")).toHaveValue("");
  });

  test("nothing of the codes is kept in the browser", async () => {
    const { user } = show({ [CREATE]: json(201, created()) });
    await create(user);

    expect(window.localStorage).toHaveLength(0);
    expect(window.sessionStorage).toHaveLength(0);
    expect(document.cookie).toBe("");
    expect(navigation.href).toBe("/admin/codes");
  });
});

describe("leaving the page while the codes are on it asks first", () => {
  const unload = () => {
    const event = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(event);
    return event.defaultPrevented;
  };
  const back = () =>
    act(() => {
      window.dispatchEvent(new PopStateEvent("popstate"));
    });

  test("the browser asks before the tab is closed or reloaded, and only while there are codes", async () => {
    const { user } = show({ [CREATE]: json(201, created()) });
    await screen.findByRole("table", { name: "Batches" });
    expect(unload()).toBe(false);

    const codes = await create(user);
    expect(unload()).toBe(true);

    await user.click(within(codes).getByRole("button", { name: "Done" }));
    await user.click(
      within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Close" }),
    );
    await waitFor(() => expect(screen.queryByText(SAVE_NOW)).not.toBeInTheDocument());
    expect(unload()).toBe(false);
  });

  test("a link to another screen is held back; staying keeps the codes", async () => {
    const { user } = show({ [CREATE]: json(201, created()) });
    await create(user);

    await user.click(screen.getByRole("link", { name: "Risk" }));

    const question = await screen.findByRole("alertdialog", { name: "Leave this page?" });
    expect(question).toHaveAccessibleDescription("These codes cannot be shown again.");
    expect(navigation.pathname).toBe("/admin/codes");

    await user.click(within(question).getByRole("button", { name: "Stay" }));

    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(shownCodes()).toContain("4F7K-Q2M9-XAB3");
    expect(navigation.pathname).toBe("/admin/codes");
    await waitFor(() => expect(screen.getByRole("link", { name: "Risk" })).toHaveFocus());
  });

  test("leaving goes where the link led", async () => {
    const { user } = show({ [CREATE]: json(201, created()) });
    await create(user);

    await user.click(screen.getByRole("link", { name: "Wallet, 240 tokens" }));
    const question = await screen.findByRole("alertdialog", { name: "Leave this page?" });
    const leave = within(question).getByRole("button", { name: "Leave" });
    expect(leave).toHaveAttribute("data-variant", "destructive");
    await user.click(leave);

    expect(navigation.pathname).toBe("/wallet");
    expect(navigation.router.replace).toHaveBeenCalledWith("/wallet");
  });

  test("the back button is held too: one step back stays on the page and asks", async () => {
    const { user } = show({ [CREATE]: json(201, created()) });
    await create(user);
    expect(window.history.pushState).toHaveBeenCalledTimes(1);

    back();

    const question = await screen.findByRole("alertdialog", { name: "Leave this page?" });
    expect(shownCodes()).toContain("4F7K-Q2M9-XAB3");
    await user.click(within(question).getByRole("button", { name: "Stay" }));

    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    // Guarded again, with another entry of its own.
    expect(window.history.pushState).toHaveBeenCalledTimes(2);
    expect(window.history.back).not.toHaveBeenCalled();
    // The back button is not on the page: the focus goes to the codes the question was about.
    await waitFor(() =>
      expect(screen.getByRole("heading", { level: 2, name: "Katerbrunch" })).toHaveFocus(),
    );

    back();
    await user.click(
      within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Leave" }),
    );
    expect(window.history.back).toHaveBeenCalledTimes(1);
  });

  test("links are not held while there are no codes", async () => {
    const { user } = show();
    await screen.findByRole("table", { name: "Batches" });

    await user.click(screen.getByRole("link", { name: "Risk" }));

    expect(navigation.pathname).toBe("/admin/risk");
    expect(window.history.pushState).not.toHaveBeenCalled();
  });
});

describe("Revoke", () => {
  test("asks in an alert-dialog that states the consequence, and sends no list of codes", async () => {
    const { api, user } = show({
      [REVOKE]: json(200, {
        batch: codeBatch({
          active_count: 0,
          revoked_count: 14,
          revoked_at: "2026-10-05T08:00:00Z",
        }),
        revoked_count: 14,
      }),
    });
    const table = await screen.findByRole("table", { name: "Batches" });
    api.on(
      BATCHES,
      json(200, {
        items: [
          codeBatch({ active_count: 0, revoked_count: 14, revoked_at: "2026-10-05T08:00:00Z" }),
        ],
      }),
    );

    await user.click(within(table).getByRole("button", { name: "Revoke Welcome week" }));

    const dialog = await screen.findByRole("alertdialog", { name: "Revoke batch" });
    expect(dialog).toHaveAccessibleDescription(
      "Every code of this batch that has not been redeemed stops working.",
    );
    expect(within(dialog).getByText("Welcome week")).toBeInTheDocument();
    const revoke = within(dialog).getByRole("button", { name: "Revoke" });
    expect(revoke).toHaveAttribute("data-variant", "destructive");
    expect(api.calls(REVOKE)).toHaveLength(0);
    await user.click(revoke);

    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(api.lastCall(REVOKE)?.path).toBe(`/api/admin/code-batches/${BATCH_ID}/revoke`);
    expect(api.lastCall(REVOKE)?.rawBody).toBe("{}");
    // The table was read again and shows what became of the batch.
    await waitFor(() =>
      expect(
        within(screen.getByRole("table", { name: "Batches" })).queryByRole("button"),
      ).not.toBeInTheDocument(),
    );
    expect(api.calls(BATCHES)).toHaveLength(2);
  });

  test("Cancel revokes nothing and gives the focus back to the button", async () => {
    const { api, user } = show();
    const table = await screen.findByRole("table", { name: "Batches" });
    const button = within(table).getByRole("button", { name: "Revoke Welcome week" });

    await user.click(button);
    const dialog = await screen.findByRole("alertdialog", { name: "Revoke batch" });
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(api.calls(REVOKE)).toHaveLength(0);
    await waitFor(() => expect(button).toHaveFocus());
  });

  test("a refusal shows the API's message in the dialog; two taps are one request", async () => {
    const answer = deferred();
    const { api, user } = show();
    api.on(REVOKE, answer.handler);
    const table = await screen.findByRole("table", { name: "Batches" });

    await user.click(within(table).getByRole("button", { name: "Revoke Welcome week" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Revoke batch" });
    await user.click(within(dialog).getByRole("button", { name: "Revoke" }));
    await user.click(within(dialog).getByRole("button", { name: "Revoke" }));

    expect(within(dialog).getByRole("button", { name: "Revoke" })).toHaveAttribute(
      "aria-busy",
      "true",
    );
    expect(api.calls(REVOKE)).toHaveLength(1);

    answer.resolve(apiError(404, "NOT_FOUND", "Not found."));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Not found.");
    expect(screen.getByRole("alertdialog", { name: "Revoke batch" })).toBeInTheDocument();
  });
});
