import { mutationOptions } from "@tanstack/react-query";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { type FormEvent, useState } from "react";
import { describe, expect, test, vi } from "vitest";

import { call } from "@/lib/api/client";
import { apiError, deferred, json, mockApi, type Route } from "@/test/http";
import { createTestQueryClient, renderWithProviders } from "@/test/render";

import { useSubmit } from "./submit";

const REDEEM: Route = "POST /api/codes/redeem";

/** A write as `lib/api/mutations.ts` defines them: no key of its own. */
const redeem = mutationOptions({
  mutationFn: (code: string) => call((api) => api.POST("/api/codes/redeem", { body: { code } })),
});

/** The same write with a key: its guard holds for every surface that sends it. */
const redeemAnywhere = { ...redeem, mutationKey: ["redeem-code"] };

function Form({
  write = redeem,
  onSent,
  onSuccess,
}: {
  write?: typeof redeem;
  /** What `mutate` returned, for every submit. */
  onSent?: (sent: boolean) => void;
  onSuccess?: () => void;
}) {
  const submit = useSubmit(write);
  const [submits, setSubmits] = useState(0);

  const send = (event: FormEvent) => {
    event.preventDefault();
    setSubmits((count) => count + 1);
    const sent = submit.mutate("4F7K-Q2M9-XAB3", { onSuccess });
    onSent?.(sent);
  };

  return (
    <form onSubmit={send}>
      <button type="submit" disabled={submit.isPending}>
        Redeem
      </button>
      <output>{submits}</output>
      {submit.error !== null && <p role="alert">{submit.error.message}</p>}
    </form>
  );
}

const form = () => screen.getByRole("button", { name: "Redeem" }).closest("form")!;

/** Two submit events before the screen has drawn anything in between. */
function submitTwiceAtOnce() {
  act(() => {
    form().dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    form().dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  });
}

describe("useSubmit", () => {
  test("two submits in one moment are one request: the second is stopped where it is made", async () => {
    const answer = deferred();
    const api = mockApi({ [REDEEM]: answer.handler });
    const onSent = vi.fn();
    renderWithProviders(<Form onSent={onSent} />);

    submitTwiceAtOnce();

    // Both events reached the handler: the button was not disabled between them.
    expect(screen.getByRole("status")).toHaveTextContent("2");
    expect(onSent.mock.calls).toEqual([[true], [false]]);
    await waitFor(() => expect(api.calls(REDEEM)).toHaveLength(1));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(api.calls(REDEEM)).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Redeem" })).toBeDisabled();
  });

  test("sends again once the request has its answer, and passes the callbacks of the call on", async () => {
    const api = mockApi({ [REDEEM]: json(200, { value_cr: "35", balance_cr: "275" }) });
    const onSuccess = vi.fn();
    const onSent = vi.fn();
    const { user } = renderWithProviders(<Form onSent={onSent} onSuccess={onSuccess} />);

    await user.click(screen.getByRole("button", { name: "Redeem" }));
    await waitFor(() => expect(onSuccess).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByRole("button", { name: "Redeem" })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "Redeem" }));

    await waitFor(() => expect(onSuccess).toHaveBeenCalledTimes(2));
    expect(onSent.mock.calls).toEqual([[true], [true]]);
    expect(api.calls(REDEEM)).toHaveLength(2);
    expect(api.lastCall(REDEEM)?.body).toEqual({ code: "4F7K-Q2M9-XAB3" });
  });

  test("a refusal is an answer: the error is shown and the next submit goes out", async () => {
    const api = mockApi({
      [REDEEM]: apiError(422, "CODE_INVALID", "This code is not valid."),
    });
    const { user } = renderWithProviders(<Form />);

    await user.click(screen.getByRole("button", { name: "Redeem" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("This code is not valid.");

    await waitFor(() => expect(screen.getByRole("button", { name: "Redeem" })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "Redeem" }));
    await waitFor(() => expect(api.calls(REDEEM)).toHaveLength(2));
  });

  test("the guard is each form's own: two forms send one request each", async () => {
    const api = mockApi({ [REDEEM]: deferred().handler });
    const user = userEvent.setup();
    renderWithProviders(
      <>
        <Form />
        <Form />
      </>,
    );
    const [first, second] = screen.getAllByRole("button", { name: "Redeem" });

    await user.click(first!);
    await user.click(second!);

    await waitFor(() => expect(api.calls(REDEEM)).toHaveLength(2));
  });

  test("a write that names its key is guarded wherever it is sent from, also after its form was closed and opened again", async () => {
    const answer = deferred();
    const api = mockApi({ [REDEEM]: answer.handler });
    const client = createTestQueryClient();
    const first = renderWithProviders(<Form write={redeemAnywhere} />, { client });
    await first.user.click(screen.getByRole("button", { name: "Redeem" }));
    await waitFor(() => expect(api.calls(REDEEM)).toHaveLength(1));
    first.unmount();

    // Opened again while the request is out: the button shows it, and a submit sends nothing.
    const onSent = vi.fn();
    renderWithProviders(<Form write={redeemAnywhere} onSent={onSent} />, { client });
    expect(screen.getByRole("button", { name: "Redeem" })).toBeDisabled();
    act(() => {
      form().dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    expect(onSent).toHaveBeenCalledWith(false);
    expect(api.calls(REDEEM)).toHaveLength(1);

    answer.resolve(json(200, { value_cr: "35", balance_cr: "275" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Redeem" })).toBeEnabled());
  });
});
