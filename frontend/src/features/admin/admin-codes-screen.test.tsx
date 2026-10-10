import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";

import { BATCH_ID, codeBatch, codeBatchCodes, codeBatchCreated, config, me } from "@/test/fixtures";
import { apiError, deferred, type Handler, json, mockApi, noAnswer, type Route } from "@/test/http";
import { navigation } from "@/test/navigation";
import { renderWithProviders } from "@/test/render";

import { AdminCodesScreen } from "./admin-codes-screen";
import { browser } from "./browser";

/* What a QR holds cannot be read off its picture: the test looks at what it was given. */
vi.mock("qrcode.react", () => ({
  QRCodeSVG: ({
    value,
    marginSize,
    "aria-label": label,
  }: {
    value: string;
    marginSize?: number;
    "aria-label"?: string;
  }) => <svg role="img" aria-label={label} data-value={value} data-margin={marginSize} />,
}));

const BATCHES: Route = "GET /api/admin/code-batches";
const CREATE: Route = "POST /api/admin/code-batches";
const REVOKE: Route = "POST /api/admin/code-batches/{batch_id}/revoke";
const CODES: Route = "GET /api/admin/code-batches/{batch_id}/codes";

const UNANSWERED =
  'We did not get an answer. If the batch is in the table below, "View codes" shows its codes.';
const RECORDED = "Viewing codes is recorded.";
const NONE_LEFT = "Every code of this batch is redeemed or revoked.";

const admin = { me: me({ role: "ADMIN" }) };
type User = ReturnType<typeof renderWithProviders>["user"];

const katerbrunch = () =>
  codeBatch({
    id: "created-batch",
    label: "Katerbrunch",
    value_cr: "12.5",
    count: 2,
    active_count: 2,
    redeemed_count: 0,
    total_value_cr: "25",
    expires_at: "2026-12-31T22:59:00Z",
  });
const created = () => codeBatchCreated({ batch: katerbrunch() });

function show(routes: Partial<Record<Route, Handler>> = {}) {
  const api = mockApi({ [BATCHES]: json(200, { items: [codeBatch()] }), ...routes });
  const rendered = renderWithProviders(<AdminCodesScreen />, admin);
  return { api, ...rendered };
}

/** What lets go of a file that was offered for saving. */
const releaseFile = vi.fn();

const newBatch = () => screen.getByRole("button", { name: "New batch" });
const sheet = () => document.querySelector<HTMLElement>("[data-slot=print-sheet]");
const cards = () => [...(sheet()?.querySelectorAll<HTMLElement>("[data-slot=print-card]") ?? [])];
const shownCodes = (dialog: HTMLElement) =>
  within(within(dialog).getByRole("list", { name: "Codes" }))
    .getAllByRole("listitem")
    .map((item) => item.textContent);
const gone = (name: string) =>
  waitFor(() => expect(screen.queryByRole("dialog", { name })).not.toBeInTheDocument());

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

/** Creates a batch and waits until the dialog with its codes stands. */
async function create(user: User) {
  const dialog = await openForm(user);
  await fillIn(user, dialog);
  await user.click(within(dialog).getByRole("button", { name: "Create batch" }));
  return screen.findByRole("dialog", { name: "Katerbrunch" });
}

/** Opens "View codes" of the batch in the table and waits for its dialog. */
async function view(user: User, label = "Welcome week") {
  await screen.findByRole("table", { name: "Batches" });
  await user.click(screen.getByRole("button", { name: `View codes of ${label}` }));
  return screen.findByRole("dialog", { name: label });
}

/** "Print QR sheet", once the sheet is laid out and the button has stopped waiting. */
async function printButton(dialog: HTMLElement) {
  const button = within(dialog).getByRole("button", { name: "Print QR sheet" });
  await waitFor(() => expect(button).toBeEnabled());
  return button;
}

beforeEach(() => {
  releaseFile.mockClear();
  navigation.go("/admin/codes");
  vi.spyOn(browser, "saveFile").mockImplementation(() => releaseFile);
  vi.spyOn(browser, "print").mockImplementation(() => {});
});

describe("Admin: code batches", () => {
  test("lists the batches with what became of their codes, as the API sent it, each with View codes and Revoke", async () => {
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
    // The two buttons stand at the end of the row, in this order.
    expect(
      within(row!)
        .getAllByRole("button")
        .map((button) => [button.textContent, button.getAttribute("aria-label")]),
    ).toEqual([
      ["View codes", "View codes of Welcome week"],
      ["Revoke", "Revoke Welcome week"],
    ]);
    expect(within(row!).getByRole("button", { name: "Revoke Welcome week" })).toBeEnabled();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Codes");
    expect(screen.getByRole("link", { name: "Codes" })).toHaveAttribute("aria-current", "page");
  });

  test("Revoke is enabled while can_revoke of the batch is true: the server says, and nothing else is looked at", async () => {
    show({
      [BATCHES]: json(200, {
        items: [
          // Codes that look active, and the server says a revocation would stop none.
          codeBatch({ id: "a", label: "Nothing to stop", active_count: 14, can_revoke: false }),
          // Revoked as a whole once, and the server says a code is left.
          codeBatch({
            id: "b",
            label: "Something left",
            active_count: 0,
            revoked_at: "2026-10-02T08:00:00Z",
            can_revoke: true,
          }),
        ],
      }),
    });

    await screen.findByRole("table", { name: "Batches" });
    expect(screen.getByRole("button", { name: "Revoke Nothing to stop" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Revoke Something left" })).toBeEnabled();
    // The codes of either can be looked at.
    expect(screen.getByRole("button", { name: "View codes of Nothing to stop" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "View codes of Something left" })).toBeEnabled();
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

  test("there is no sheet for the printer and no code on the page while no codes are shown", async () => {
    const { api } = show();
    await screen.findByRole("table", { name: "Batches" });

    expect(sheet()).toBeNull();
    // The table of batches holds no code, and nothing asked for one.
    expect(api.calls(CODES)).toHaveLength(0);
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
    expect(within(dialog).getByRole("button", { name: "Close" })).toBeEnabled();
  });

  test("states its bounds from /config: the length of a label, the most codes, and what a code may be for", async () => {
    mockApi({ [BATCHES]: json(200, { items: [codeBatch()] }) });
    const { user } = renderWithProviders(<AdminCodesScreen />, {
      ...admin,
      config: config({
        code_label_max_length: 40,
        code_batch_max_count: 1200,
        code_default_value_cr: "40",
        code_min_value_cr: "2.5",
        code_max_value_cr: "1250",
      }),
    });
    const dialog = await openForm(user);

    const label = within(dialog).getByLabelText("Label");
    expect(label).toHaveAttribute("maxlength", "40");
    expect(within(dialog).getByText("0 / 40")).toBeInTheDocument();
    await user.type(label, "Kater");
    expect(within(dialog).getByText("5 / 40")).toBeInTheDocument();

    expect(within(dialog).getByLabelText("Number of codes")).toHaveAccessibleDescription(
      "At most 1,200 codes",
    );
    const value = within(dialog).getByLabelText("Tokens per code");
    expect(value).toHaveValue("40");
    expect(value).toHaveAccessibleDescription("Between 2.5 and 1,250 tokens");
  });

  test("the button waits until everything is filled in, and a number of codes takes digits alone", async () => {
    const { user } = show();
    const dialog = await openForm(user);
    const go = within(dialog).getByRole("button", { name: "Create batch" });
    const count = within(dialog).getByLabelText("Number of codes");

    await user.type(within(dialog).getByLabelText("Label"), "Katerbrunch");
    await user.type(within(dialog).getByLabelText("Expires"), "2026-12-31T23:59");
    expect(go).toBeDisabled();

    // A letter, a point and a minus are not taken: the field shows what is sent.
    await user.type(count, "2o.-5");
    expect(count).toHaveValue("25");
    expect(count).toHaveAttribute("inputmode", "numeric");
    expect(go).toBeEnabled();

    await user.clear(within(dialog).getByLabelText("Tokens per code"));
    expect(go).toBeDisabled();
  });

  test("sends the count as a number, the value as the string that was typed and the expiry with the zone's offset", async () => {
    const { api, user } = show({ [CREATE]: json(201, created()) });
    const dialog = await openForm(user);

    await fillIn(user, dialog);
    await user.clear(within(dialog).getByLabelText("Tokens per code"));
    await user.type(within(dialog).getByLabelText("Tokens per code"), "12,50");
    await user.click(within(dialog).getByRole("checkbox", { name: "One code per person" }));
    await user.click(within(dialog).getByRole("button", { name: "Create batch" }));

    await gone("New batch");
    // Never a number made from the value: "12.50" would have become 12.5.
    expect(api.lastCall(CREATE)?.rawBody).toBe(
      '{"label":"Katerbrunch","count":2,"value_cr":"12.50","expires_at":"2026-12-31T23:59:00+01:00","one_per_user":false}',
    );
  });

  test("the bounds are for the reader: a value outside them is sent, and the API's refusal stands on its field", async () => {
    const { api, user } = show({
      [CREATE]: apiError(422, "VALIDATION_ERROR", "A code is for between 5 and 500 tokens.", {
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
      /A code is for between 5 and 500 tokens\./,
    );
    expect(within(dialog).getByLabelText("Label")).toHaveValue("Katerbrunch");
    expect(screen.queryByRole("dialog", { name: "Katerbrunch" })).not.toBeInTheDocument();
  });

  test.each([
    ["count", "Number of codes", "A batch has at most 500 codes."],
    ["expires_at", "Expires", "The codes have to expire in the future."],
    ["label", "Label", "A label has at most 100 characters."],
  ])("a refusal of %s stands on its field, in the API's words", async (field, label, message) => {
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

  test("a refusal of one_per_user stands under the checkbox", async () => {
    const { user } = show({
      [CREATE]: apiError(422, "VALIDATION_ERROR", "The request is not valid.", {
        errors: [{ loc: ["body", "one_per_user"], msg: "Say yes or no.", type: "x" }],
      }),
    });
    const dialog = await openForm(user);

    await fillIn(user, dialog);
    await user.click(within(dialog).getByRole("button", { name: "Create batch" }));

    const box = within(dialog).getByRole("checkbox", { name: "One code per person" });
    await waitFor(() => expect(box).toBeInvalid());
    expect(box).toHaveAccessibleDescription("Say yes or no.");
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

  test("the button is disabled from tap until the answer: two taps are one batch, and the dialog cannot be closed meanwhile", async () => {
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
    expect(within(dialog).getByRole("button", { name: "Close" })).toBeDisabled();
    expect(api.calls(CREATE)).toHaveLength(1);

    answer.resolve(json(201, created()));
    expect(await screen.findByRole("dialog", { name: "Katerbrunch" })).toBeInTheDocument();
    expect(api.calls(CREATE)).toHaveLength(1);
  });

  test("two submits in one moment are one batch: the second is stopped where it is made", async () => {
    const answer = deferred();
    const { api, user } = show({ [CREATE]: answer.handler });
    const dialog = await openForm(user);
    await fillIn(user, dialog);
    const form = dialog.querySelector("form")!;

    // Enter and a click, or a tap that lands twice: no render lies between the two, so
    // the button was not disabled for the second.
    act(() => {
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });

    await waitFor(() => expect(api.calls(CREATE)).toHaveLength(1));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(api.calls(CREATE)).toHaveLength(1);

    answer.resolve(json(201, created()));
    expect(await screen.findByRole("dialog", { name: "Katerbrunch" })).toBeInTheDocument();
    expect(api.calls(CREATE)).toHaveLength(1);
  });

  test("without an answer the table is read again before anything else, and View codes shows the codes of a batch that arrived", async () => {
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

    // The batch did arrive: it is in the table.
    reread.resolve(json(200, { items: [katerbrunch(), codeBatch()] }));

    expect(await screen.findByText(UNANSWERED)).toBeInTheDocument();
    await gone("New batch");
    expect(
      within(screen.getByRole("table", { name: "Batches" })).getByText("Katerbrunch"),
    ).toBeInTheDocument();
    expect(api.calls(CREATE)).toHaveLength(1);
    expect(newBatch()).toBeEnabled();

    // Its codes are not lost: "View codes" asks for them.
    api.on(CODES, json(200, codeBatchCodes({ batch: katerbrunch() })));
    const codes = await view(user, "Katerbrunch");
    await waitFor(() => expect(shownCodes(codes)).toEqual(["4F7K-Q2M9-XAB3", "T8WN-3CB4-Z6PD"]));
    expect(api.lastCall(CODES)?.path).toBe("/api/admin/code-batches/created-batch/codes");
    await user.click(within(codes).getByRole("button", { name: "Done" }));
    await gone("Katerbrunch");

    // Trying again starts from what was typed, and the banner has done its work.
    await user.click(newBatch());
    const again = await screen.findByRole("dialog", { name: "New batch" });
    expect(within(again).getByLabelText("Label")).toHaveValue("Katerbrunch");
    expect(within(again).queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByText(UNANSWERED)).not.toBeInTheDocument();
  });
});

describe("the codes of a batch that was just created", () => {
  test("open by themselves in a dialog titled with the batch's label, from the answer and without a read", async () => {
    const { api, user } = show({ [CREATE]: json(201, created()) });

    const dialog = await create(user);

    expect(within(dialog).getByRole("heading", { level: 2, name: "Katerbrunch" })).toBeVisible();
    expect(within(dialog).getByText("2 codes")).toBeInTheDocument();
    expect(shownCodes(dialog)).toEqual(["4F7K-Q2M9-XAB3", "T8WN-3CB4-Z6PD"]);
    // The line, the two buttons, the codes, then Done.
    const order = [
      within(dialog).getByText("2 codes"),
      within(dialog).getByRole("button", { name: "Download CSV" }),
      within(dialog).getByRole("button", { name: "Print QR sheet" }),
      within(dialog).getByRole("list", { name: "Codes" }),
      within(dialog).getByRole("button", { name: "Done" }),
    ];
    for (let index = 1; index < order.length; index += 1) {
      expect(
        order[index - 1]!.compareDocumentPosition(order[index]!) & Node.DOCUMENT_POSITION_FOLLOWING,
      ).not.toBe(0);
    }
    expect(within(dialog).getByRole("button", { name: "Close" })).toBeEnabled();
    // The box of the codes scrolls, and the keyboard reaches it.
    expect(within(dialog).getByRole("list", { name: "Codes" })).toHaveAttribute("tabindex", "0");
    // The codes came with the answer: nothing was read, so nothing was recorded.
    expect(api.calls(CODES)).toHaveLength(0);
    expect(within(dialog).queryByText(RECORDED)).not.toBeInTheDocument();
    // The form is gone, and the table behind was read again.
    expect(screen.queryByRole("dialog", { name: "New batch" })).not.toBeInTheDocument();
    await waitFor(() => expect(api.calls(BATCHES)).toHaveLength(2));
  });

  test("one code is one code", async () => {
    const { user } = show({
      [CREATE]: json(
        201,
        codeBatchCreated({
          batch: katerbrunch(),
          codes: [{ code: "4F7KQ2M9XAB3", redeem_url: "http://localhost:8080/wallet?code=4F7K" }],
        }),
      ),
    });

    const dialog = await create(user);

    expect(within(dialog).getByText("1 code")).toBeInTheDocument();
  });

  test("Download CSV makes the file in the browser from the answer", async () => {
    const { api, user } = show({ [CREATE]: json(201, created()) });
    const dialog = await create(user);
    await waitFor(() => expect(api.calls(BATCHES)).toHaveLength(2));
    const requests = api.requests.length;

    await user.click(within(dialog).getByRole("button", { name: "Download CSV" }));

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
    // The file stays readable while the dialog shows the codes.
    expect(releaseFile).not.toHaveBeenCalled();
  });

  test("Done closes the dialog, and the codes are gone from the page with every file made of them", async () => {
    const { user } = show({ [CREATE]: json(201, created()) });
    const dialog = await create(user);
    await user.click(within(dialog).getByRole("button", { name: "Download CSV" }));
    await user.click(within(dialog).getByRole("button", { name: "Download CSV" }));
    await printButton(dialog);
    expect(document.body.innerHTML).toContain("4F7KQ2M9XAB3");

    await user.click(within(dialog).getByRole("button", { name: "Done" }));

    await gone("Katerbrunch");
    expect(releaseFile).toHaveBeenCalledTimes(2);
    expect(sheet()).toBeNull();
    expect(document.body.innerHTML).not.toContain("4F7KQ2M9XAB3");
    expect(document.body.innerHTML).not.toContain("4F7K-Q2M9-XAB3");
    // No question stands in the way: the codes can be looked at again.
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  test("the close cross closes it as Done does, and the focus goes back to New batch", async () => {
    const { user } = show({ [CREATE]: json(201, created()) });
    const dialog = await create(user);

    await user.click(within(dialog).getByRole("button", { name: "Close" }));

    await gone("Katerbrunch");
    expect(sheet()).toBeNull();
    await waitFor(() => expect(newBatch()).toHaveFocus());

    // The form starts afresh for the next batch.
    await user.click(newBatch());
    const form = await screen.findByRole("dialog", { name: "New batch" });
    expect(within(form).getByLabelText("Label")).toHaveValue("");
  });

  test("once the dialog has closed the answer is let go of: View codes asks the API", async () => {
    const batch = katerbrunch();
    const { api, client, user } = show({
      // The table as it is read again after the creation: the batch is in it.
      [BATCHES]: json(200, { items: [batch, codeBatch()] }),
      [CREATE]: json(201, created()),
      [CODES]: json(200, codeBatchCodes({ batch })),
    });
    const dialog = await create(user);
    await user.click(within(dialog).getByRole("button", { name: "Done" }));
    await gone("Katerbrunch");
    // The focus is back on the page: the dialog is gone for good.
    await waitFor(() => expect(newBatch()).toHaveFocus());
    expect(api.calls(CODES)).toHaveLength(0);
    // The answer was the one place that held the codes: with the dialog gone, nothing holds it.
    const held = () =>
      JSON.stringify([
        client
          .getMutationCache()
          .getAll()
          .map((mutation) => mutation.state.data),
        client
          .getQueryCache()
          .getAll()
          .map((query) => query.state.data),
      ]);
    await waitFor(() => expect(held()).not.toContain("4F7KQ2M9XAB3"));
    await screen.findByRole("button", { name: "View codes of Katerbrunch" });

    const again = await view(user, "Katerbrunch");

    await waitFor(() => expect(api.calls(CODES)).toHaveLength(1));
    expect(await within(again).findByText(RECORDED)).toBeInTheDocument();
    expect(shownCodes(again)).toEqual(["4F7K-Q2M9-XAB3", "T8WN-3CB4-Z6PD"]);
  });

  test("nothing of the codes is kept in the browser or put into the address", async () => {
    const { user } = show({ [CREATE]: json(201, created()) });
    const dialog = await create(user);
    await printButton(dialog);

    expect(window.localStorage).toHaveLength(0);
    expect(window.sessionStorage).toHaveLength(0);
    expect(document.cookie).toBe("");
    expect(navigation.href).toBe("/admin/codes");
  });
});

describe("View codes", () => {
  test("asks for the codes of the batch that can be redeemed, shows them and says that viewing is recorded", async () => {
    const { api, user } = show({ [CODES]: json(200, codeBatchCodes()) });

    const dialog = await view(user);

    expect(await within(dialog).findByText("2 codes")).toBeInTheDocument();
    expect(shownCodes(dialog)).toEqual(["4F7K-Q2M9-XAB3", "T8WN-3CB4-Z6PD"]);
    expect(within(dialog).getByText(RECORDED)).toHaveClass("type-caption");
    expect(within(dialog).getByRole("button", { name: "Download CSV" })).toBeEnabled();
    expect(api.calls(CODES)).toHaveLength(1);
    expect(api.lastCall(CODES)?.path).toBe(`/api/admin/code-batches/${BATCH_ID}/codes`);
    expect(api.lastCall(CODES)?.query.toString()).toBe("");
  });

  test("one opening is one read: the focus of the window and a table that is read again ask for nothing", async () => {
    const { api, user } = show({ [CODES]: json(200, codeBatchCodes()) });
    api.on("GET /api/me", json(200, admin.me));
    const dialog = await view(user);
    await within(dialog).findByText("2 codes");

    window.dispatchEvent(new Event("visibilitychange"));

    await waitFor(() => expect(api.calls(BATCHES)).toHaveLength(2));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(api.calls(CODES)).toHaveLength(1);
  });

  test("closing the dialog forgets the codes, and View codes brings them back with another read", async () => {
    const { api, user } = show({ [CODES]: json(200, codeBatchCodes()) });
    const opener = await screen.findByRole("button", { name: "View codes of Welcome week" });
    const dialog = await view(user);
    await within(dialog).findByText("2 codes");

    await user.click(within(dialog).getByRole("button", { name: "Done" }));

    await gone("Welcome week");
    expect(document.body.innerHTML).not.toContain("4F7K");
    expect(sheet()).toBeNull();
    // The focus is back on the button of the row.
    await waitFor(() => expect(opener).toHaveFocus());

    // One of the two was redeemed meanwhile: the dialog shows what the API says now.
    api.on(
      CODES,
      json(
        200,
        codeBatchCodes({
          codes: [
            { code: "T8WN3CB4Z6PD", redeem_url: "http://localhost:8080/wallet?code=T8WN3CB4Z6PD" },
          ],
        }),
      ),
    );
    const again = await view(user);

    expect(await within(again).findByText("1 code")).toBeInTheDocument();
    expect(shownCodes(again)).toEqual(["T8WN-3CB4-Z6PD"]);
    expect(api.calls(CODES)).toHaveLength(2);
  });

  test("while the codes are on their way the box shows skeleton lines and the two buttons are disabled", async () => {
    const answer = deferred();
    const { user } = show({ [CODES]: answer.handler });

    const dialog = await view(user);

    // The title is the batch's label from the first moment.
    expect(within(dialog).getByRole("heading", { level: 2, name: "Welcome week" })).toBeVisible();
    expect(dialog.querySelector("[data-slot=codes-skeleton]")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Download CSV" })).toBeDisabled();
    expect(within(dialog).getByRole("button", { name: "Print QR sheet" })).toBeDisabled();
    expect(within(dialog).queryByRole("list", { name: "Codes" })).not.toBeInTheDocument();
    // The one control that can be used takes the focus.
    const done = within(dialog).getByRole("button", { name: "Done" });
    expect(done).toBeEnabled();
    await waitFor(() => expect(done).toHaveFocus());

    answer.resolve(json(200, codeBatchCodes()));

    expect(await within(dialog).findByText("2 codes")).toBeInTheDocument();
    expect(dialog.querySelector("[data-slot=codes-skeleton]")).not.toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Download CSV" })).toBeEnabled();
    // The buttons are the elements they were: the focus has not been thrown back to the dialog.
    expect(within(dialog).getByRole("button", { name: "Done" })).toBe(done);
    expect(done).toHaveFocus();
  });

  test("a failure shows the API's message in the dialog with Try again, which reads once more", async () => {
    const { api, user } = show({
      [CODES]: apiError(500, "INTERNAL_ERROR", "Something went wrong. Try again."),
    });

    const dialog = await view(user);

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Something went wrong. Try again.",
    );
    expect(within(dialog).getByRole("button", { name: "Download CSV" })).toBeDisabled();
    expect(within(dialog).getByRole("button", { name: "Print QR sheet" })).toBeDisabled();
    expect(browser.print).not.toHaveBeenCalled();

    api.on(CODES, json(200, codeBatchCodes()));
    await user.click(within(dialog).getByRole("button", { name: "Try again" }));

    expect(await within(dialog).findByText("2 codes")).toBeInTheDocument();
    expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();
    expect(api.calls(CODES)).toHaveLength(2);
  });

  test("a batch the API does not know says so in the dialog", async () => {
    const { user } = show({ [CODES]: apiError(404, "NOT_FOUND", "Not found.") });

    const dialog = await view(user);

    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Not found.");
  });

  test("when none is left the dialog says so, and its two buttons are disabled", async () => {
    const { user } = show({
      [CODES]: json(
        200,
        codeBatchCodes({ batch: codeBatch({ active_count: 0, redeemed_count: 20 }), codes: [] }),
      ),
    });

    const dialog = await view(user);

    expect(await within(dialog).findByText(NONE_LEFT)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Download CSV" })).toBeDisabled();
    const print = within(dialog).getByRole("button", { name: "Print QR sheet" });
    expect(print).toBeDisabled();
    // Disabled, not waiting: there is no sheet to lay out.
    expect(print).not.toHaveAttribute("aria-busy");
    expect(within(dialog).queryByRole("list", { name: "Codes" })).not.toBeInTheDocument();
    expect(cards()).toHaveLength(0);
    expect(within(dialog).getByText(RECORDED)).toBeInTheDocument();
  });

  test("a batch that has expired has no code to show either, and the dialog says which of the two it is from the batch's flag", async () => {
    const { user } = show({
      [CODES]: json(
        200,
        // Codes that were never redeemed or revoked, and the API lists none: the batch expired.
        codeBatchCodes({ batch: codeBatch({ expired: true, active_count: 14 }), codes: [] }),
      ),
    });

    const dialog = await view(user);

    expect(
      await within(dialog).findByText("The codes of this batch have expired."),
    ).toBeInTheDocument();
    expect(within(dialog).queryByText(NONE_LEFT)).not.toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Download CSV" })).toBeDisabled();
    expect(within(dialog).getByRole("button", { name: "Print QR sheet" })).toBeDisabled();
  });

  test("the CSV is made from the codes the dialog shows, with what their batch says", async () => {
    const { user } = show({
      [CODES]: json(200, codeBatchCodes({ batch: codeBatch({ label: "=Welcome, week" }) })),
    });
    const dialog = await view(user);
    await within(dialog).findByText("2 codes");

    await user.click(within(dialog).getByRole("button", { name: "Download CSV" }));

    expect(browser.saveFile).toHaveBeenCalledWith(
      "codes-welcome-week.csv",
      [
        "code,redeem_url,label,tokens,expires_at",
        `4F7KQ2M9XAB3,http://localhost:8080/wallet?code=4F7KQ2M9XAB3,"'=Welcome, week",35,2026-12-31T22:59:00Z`,
        `T8WN3CB4Z6PD,http://localhost:8080/wallet?code=T8WN3CB4Z6PD,"'=Welcome, week",35,2026-12-31T22:59:00Z`,
        "",
      ].join("\r\n"),
      "text/csv;charset=utf-8",
    );
  });
});

describe("Print QR sheet", () => {
  test("the sheet is laid out as soon as the codes are there, and the button waits for it", async () => {
    const { user } = show({ [CREATE]: json(201, created()) });
    const dialog = await create(user);
    const button = within(dialog).getByRole("button", { name: "Print QR sheet" });

    await waitFor(() => expect(cards()).toHaveLength(2));
    await waitFor(() => expect(button).toBeEnabled());
    expect(button).not.toHaveAttribute("aria-busy");
    // Nobody asked for paper yet.
    expect(browser.print).not.toHaveBeenCalled();
  });

  test("a tap asks the browser for its print dialog in the tap itself, with the sheet on the page", async () => {
    const { api, user } = show({ [CODES]: json(200, codeBatchCodes()) });
    const dialog = await view(user);
    const button = await printButton(dialog);
    const requests = api.requests.length;
    let onThePage: { cards: number; rules: boolean; childOfBody: boolean } | null = null;
    vi.mocked(browser.print).mockImplementation(() => {
      onThePage = {
        cards: cards().length,
        rules: sheet()?.querySelector("style") !== null,
        childOfBody: sheet()?.parentElement === document.body,
      };
    });

    // One event, and nothing awaited after it: what is not called by now is not called
    // in the tap.
    fireEvent.click(button);

    expect(browser.print).toHaveBeenCalledTimes(1);
    expect(onThePage).toEqual({ cards: 2, rules: true, childOfBody: true });
    expect(api.requests).toHaveLength(requests);

    // Asked for again, it is printed again.
    fireEvent.click(button);
    expect(browser.print).toHaveBeenCalledTimes(2);
  });

  test("the sheet stands outside the app and the dialog, and its rules take everything else off the paper", async () => {
    const { user } = show({ [CREATE]: json(201, created()) });
    const dialog = await create(user);
    await printButton(dialog);

    const paper = sheet()!;
    expect(paper.parentElement).toBe(document.body);
    expect(dialog).not.toContainElement(paper);
    // On screen it is not shown; on paper it is.
    expect(paper).toHaveClass("hidden", "print:block", "text-ink");
    const rules = paper.querySelector("style")!.textContent!;
    expect(rules).toContain("@page { size: A4 portrait; margin: 12mm; }");
    expect(rules).toMatch(
      /@media print \{\s*body > :not\(\[data-slot="print-sheet"\]\) \{ display: none !important; \}\s*\}/,
    );
  });

  test("its cards hold the QR of the redeem link, the code and what it is for, under the dark logo and the batch's label", async () => {
    const { user } = show({ [CREATE]: json(201, created()) });
    const dialog = await create(user);
    await printButton(dialog);

    const paper = sheet()!;
    // The sheet is for paper alone: while the dialog is open, a screen reader is kept off it.
    expect(within(paper).getByRole("img", { name: "MarkETH", hidden: true })).toHaveAttribute(
      "src",
      expect.stringContaining("logo-lockup-dark.png"),
    );
    expect(within(paper).getByText("Katerbrunch")).toBeInTheDocument();
    expect(paper.querySelector("header")).toHaveTextContent("Valid until 31 Dec 2026, 23:59");
    expect(
      cards().map((card) => [
        card.querySelector("svg")?.getAttribute("data-value"),
        card.querySelector("svg")?.getAttribute("aria-label"),
        card.querySelector("[data-slot=code]")?.textContent,
        card.querySelector("[data-slot=amount] .sr-only")?.textContent,
      ]),
    ).toEqual([
      [
        "http://localhost:8080/wallet?code=4F7KQ2M9XAB3",
        "QR code of 4F7K-Q2M9-XAB3",
        "4F7K-Q2M9-XAB3",
        "12.5 tokens",
      ],
      [
        "http://localhost:8080/wallet?code=T8WN3CB4Z6PD",
        "QR code of T8WN-3CB4-Z6PD",
        "T8WN-3CB4-Z6PD",
        "12.5 tokens",
      ],
    ]);
    // A quiet zone of four modules around every QR, and no tile behind it.
    for (const card of cards()) {
      expect(card.querySelector("svg")).toHaveAttribute("data-margin", "4");
      expect(card.querySelector("[class*=bg-]")).toBeNull();
    }
  });

  test("printing through the browser's own menu shows the sheet too: it is on the page before anybody asks", async () => {
    const { user } = show({ [CODES]: json(200, codeBatchCodes()) });
    const dialog = await view(user);
    await printButton(dialog);

    act(() => {
      window.dispatchEvent(new Event("beforeprint"));
    });

    expect(cards()).toHaveLength(2);
    expect(sheet()).toHaveClass("print:block");
    // The browser is printing already: the page does not ask it to.
    expect(browser.print).not.toHaveBeenCalled();
  });

  test("while the codes are read, and when there are none, a print of the page is the page: the sheet has no card and no rules", async () => {
    const answer = deferred();
    const { user } = show({ [CODES]: answer.handler });
    const dialog = await view(user);

    expect(sheet()).toBeNull();

    answer.resolve(json(200, codeBatchCodes({ codes: [] })));
    await within(dialog).findByText(NONE_LEFT);

    const paper = sheet()!;
    expect(cards()).toHaveLength(0);
    expect(paper.querySelector("style")).toBeNull();
    expect(paper).toHaveClass("hidden");
    expect(paper).not.toHaveClass("print:block");
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
          can_revoke: false,
        }),
        revoked_count: 14,
      }),
    });
    const table = await screen.findByRole("table", { name: "Batches" });
    api.on(
      BATCHES,
      json(200, {
        items: [
          codeBatch({
            active_count: 0,
            revoked_count: 14,
            revoked_at: "2026-10-05T08:00:00Z",
            can_revoke: false,
          }),
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
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toBeEnabled();
    expect(within(dialog).getByRole("button", { name: "Close" })).toBeEnabled();
    expect(api.calls(REVOKE)).toHaveLength(0);
    await user.click(revoke);

    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(api.lastCall(REVOKE)?.path).toBe(`/api/admin/code-batches/${BATCH_ID}/revoke`);
    expect(api.lastCall(REVOKE)?.rawBody).toBe("{}");
    // The table was read again and shows what became of the batch: nothing is left to stop.
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Revoke Welcome week" })).toBeDisabled(),
    );
    expect(api.calls(BATCHES)).toHaveLength(2);
    // Its button cannot take the focus back: the heading of the table has it.
    await waitFor(() =>
      expect(screen.getByRole("heading", { level: 2, name: "Batches" })).toHaveFocus(),
    );
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

  test("two submits in one moment are one revocation: the second is stopped where it is made", async () => {
    const answer = deferred();
    const { api, user } = show();
    api.on(REVOKE, answer.handler);
    const table = await screen.findByRole("table", { name: "Batches" });
    await user.click(within(table).getByRole("button", { name: "Revoke Welcome week" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Revoke batch" });
    const form = dialog.querySelector("form")!;

    act(() => {
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });

    await waitFor(() => expect(api.calls(REVOKE)).toHaveLength(1));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(api.calls(REVOKE)).toHaveLength(1);
  });
});
