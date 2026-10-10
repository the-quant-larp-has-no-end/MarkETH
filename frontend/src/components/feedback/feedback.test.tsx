import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";

import { Link } from "@/components/shell";
import { ApiError, toApiError } from "@/lib/api/errors";
import { ledgerQuery, positionsQuery } from "@/lib/api/queries";
import { BEAN_DROP_MS, REDUCED_MOTION_QUERY } from "@/lib/design";
import { ledgerItem, portfolio } from "@/test/fixtures";
import { apiError, deferred, json, mockApi } from "@/test/http";
import { setMediaQuery } from "@/test/media";
import { intersect } from "@/test/observers";
import { renderWithProviders } from "@/test/render";

import { Banner, ErrorBanner } from "./banner";
import { BeanBounce, BeanDrop } from "./bean-drop";
import { EmptyState } from "./empty-state";
import { InlineError } from "./inline-error";
import { LoadMore } from "./load-more";
import { QueryState } from "./query-state";

describe("Banner", () => {
  test("a neutral banner is a status on --raised, with an icon", () => {
    render(<Banner>Trading is paused.</Banner>);

    const banner = screen.getByRole("status");
    expect(banner).toHaveTextContent("Trading is paused.");
    expect(banner).toHaveClass("bg-raised");
    expect(banner.querySelector("svg")).toBeInTheDocument();
  });

  test("an error banner is an alert on the soft red, with the red text tint", () => {
    render(<Banner tone="error">Not found.</Banner>);

    expect(screen.getByRole("alert")).toHaveClass("bg-no-soft", "text-no-text");
  });
});

describe("ErrorBanner", () => {
  test("shows the API's message as sent, and Try again", async () => {
    const onRetry = vi.fn();
    const { user } = renderWithProviders(
      <ErrorBanner
        error={toApiError(503, {
          code: "SERVICE_UNAVAILABLE",
          message: "This is temporarily unavailable. Try again shortly.",
          details: {},
        })}
        onRetry={onRetry}
      />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent(
      "This is temporarily unavailable. Try again shortly.",
    );

    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  test("has no button where asking again makes no sense", () => {
    renderWithProviders(<ErrorBanner error={ApiError.network()} />);

    expect(screen.getByRole("alert")).toHaveTextContent("No connection. Try again.");
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  test("puts the one sentence of ours in place of a message about a header", () => {
    renderWithProviders(
      <ErrorBanner
        error={toApiError(409, {
          code: "IDEMPOTENCY_KEY_REUSED",
          message: "This Idempotency-Key was already used for a different request.",
          details: {},
        })}
      />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent("Something went wrong. Try again.");
  });

  test("after RATE_LIMITED the button counts down the seconds the API named", async () => {
    vi.useFakeTimers();
    try {
      const limited = toApiError(429, {
        code: "RATE_LIMITED",
        message: "Too many attempts. Try again later.",
        details: { retry_after_seconds: 3 },
      });
      render(<ErrorBanner error={limited} onRetry={() => {}} />);

      expect(screen.getByRole("button")).toBeDisabled();
      expect(screen.getByRole("button")).toHaveTextContent("Try again in 3 s");

      await act(() => vi.advanceTimersByTimeAsync(1_100));
      expect(screen.getByRole("button")).toHaveTextContent("Try again in 2 s");

      await act(() => vi.advanceTimersByTimeAsync(2_000));

      expect(screen.getByRole("button", { name: "Try again" })).toBeEnabled();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("InlineError", () => {
  test("is an alert in the red text tint with an icon, and can be named by a field", () => {
    render(<InlineError id="field-error">This code is not valid.</InlineError>);

    const error = screen.getByRole("alert");
    expect(error).toHaveTextContent("This code is not valid.");
    expect(error).toHaveClass("text-no-text");
    expect(error).toHaveAttribute("id", "field-error");
    expect(error.querySelector("svg")).toBeInTheDocument();
  });
});

describe("EmptyState", () => {
  test("is one illustration, one sentence and at most one action", () => {
    render(
      <EmptyState illustration="empty-cup" action={<Link href="/">Browse markets</Link>}>
        No positions yet. Your cup is empty.
      </EmptyState>,
    );

    const empty = document.querySelector("[data-slot=empty-state]")!;
    expect(empty.querySelectorAll("svg")).toHaveLength(1);
    expect(empty).toHaveTextContent("No positions yet. Your cup is empty.");
    expect(screen.getByRole("link", { name: "Browse markets" })).toBeInTheDocument();
  });
});

describe("QueryState", () => {
  function Positions() {
    const query = useQuery(positionsQuery);
    return (
      <QueryState query={query} skeleton={<p>skeleton</p>}>
        {(data) => <p>total {data.total_value_cr}</p>}
      </QueryState>
    );
  }

  test("shows the skeleton, then the content", async () => {
    const answer = deferred();
    mockApi({ "GET /api/me/positions": answer.handler });
    renderWithProviders(<Positions />);

    expect(screen.getByText("skeleton")).toBeInTheDocument();

    answer.resolve(json(200, portfolio({ total_value_cr: "50.221461" })));

    expect(await screen.findByText("total 50.221461")).toBeInTheDocument();
    expect(screen.queryByText("skeleton")).not.toBeInTheDocument();
  });

  test("shows the API's message with Try again when the read failed, and reads again on tap", async () => {
    const api = mockApi({
      "GET /api/me/positions": apiError(500, "INTERNAL_ERROR", "Something went wrong."),
    });
    const { user } = renderWithProviders(<Positions />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong.");

    api.on("GET /api/me/positions", json(200, portfolio({ total_value_cr: "12" })));
    await user.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findByText("total 12")).toBeInTheDocument();
  });

  test("keeps what is on screen when reading it again fails", async () => {
    const api = mockApi({
      "GET /api/me/positions": json(200, portfolio({ total_value_cr: "12" })),
    });
    const { client } = renderWithProviders(<Positions />);
    await screen.findByText("total 12");

    api.on("GET /api/me/positions", apiError(503, "SERVICE_UNAVAILABLE", "Unavailable."));
    await client.invalidateQueries({ queryKey: positionsQuery.queryKey });

    expect(screen.getByText("total 12")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  test("lets the screen treat an error itself", async () => {
    mockApi({ "GET /api/me/positions": apiError(404, "NOT_FOUND", "Not found.") });

    function Screen() {
      const query = useQuery(positionsQuery);
      return (
        <QueryState
          query={query}
          skeleton={<p>skeleton</p>}
          onError={(error) =>
            error.code === "NOT_FOUND" ? <p>the not found screen</p> : undefined
          }
        >
          {() => <p>content</p>}
        </QueryState>
      );
    }
    renderWithProviders(<Screen />);

    expect(await screen.findByText("the not found screen")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("LoadMore", () => {
  function Ledger() {
    const query = useInfiniteQuery(ledgerQuery);
    return (
      <div>
        {query.data?.pages.flatMap((page) =>
          page.items.map((item) => <p key={item.id}>{item.description}</p>),
        )}
        <LoadMore query={query} />
      </div>
    );
  }

  const firstPage = json(200, {
    items: [ledgerItem({ id: 1, description: "Welcome tokens" })],
    next_cursor: "page-2",
  });
  const lastPage = json(200, {
    items: [ledgerItem({ id: 2, description: "Monthly tokens" })],
    next_cursor: null,
  });

  test("asks for the next page with next_cursor when the end of the list comes into view", async () => {
    const api = mockApi({
      "GET /api/me/ledger": (request) => (request.query.has("cursor") ? lastPage : firstPage),
    });
    renderWithProviders(<Ledger />);
    await screen.findByText("Welcome tokens");
    expect(api.calls("GET /api/me/ledger")).toHaveLength(1);

    act(() => intersect());

    expect(await screen.findByText("Monthly tokens")).toBeInTheDocument();
    expect(api.lastCall("GET /api/me/ledger")?.query.get("cursor")).toBe("page-2");
    // The last page is there: nothing more to load, nothing more is asked for.
    await waitFor(() =>
      expect(document.querySelector("[data-slot=load-more]")).not.toBeInTheDocument(),
    );
    act(() => intersect());
    expect(api.calls("GET /api/me/ledger")).toHaveLength(2);
  });

  test("a page that failed shows the API's message and asks again only on tap", async () => {
    const api = mockApi({
      "GET /api/me/ledger": (request) =>
        request.query.has("cursor")
          ? apiError(
              503,
              "SERVICE_UNAVAILABLE",
              "This is temporarily unavailable. Try again shortly.",
            )
          : firstPage,
    });
    const { user } = renderWithProviders(<Ledger />);
    await screen.findByText("Welcome tokens");

    act(() => intersect());

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This is temporarily unavailable. Try again shortly.",
    );
    expect(screen.getByText("Welcome tokens")).toBeInTheDocument();
    const asked = api.calls("GET /api/me/ledger").length;

    api.on("GET /api/me/ledger", lastPage);
    await user.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findByText("Monthly tokens")).toBeInTheDocument();
    expect(api.calls("GET /api/me/ledger").length).toBe(asked + 1);
  });
});

describe("BeanDrop", () => {
  const beans = () => document.querySelectorAll("[data-slot=bean-drop] [data-slot=token-icon]");

  test("with reduced motion nothing falls and the figure is the new one at once", () => {
    render(
      <BeanDrop arrival="redeemed-1">{(falling) => <p>{falling ? "240" : "275"}</p>}</BeanDrop>,
    );

    expect(beans()).toHaveLength(0);
    expect(screen.getByText("275")).toBeInTheDocument();
  });

  test("drops a few token icons into the old figure, then shows the new one", async () => {
    setMediaQuery(REDUCED_MOTION_QUERY, false);
    vi.useFakeTimers();
    try {
      render(
        <BeanDrop arrival="redeemed-1">{(falling) => <p>{falling ? "240" : "275"}</p>}</BeanDrop>,
      );

      expect(beans().length).toBeGreaterThanOrEqual(3);
      expect(beans().length).toBeLessThanOrEqual(5);
      expect(screen.getByText("240")).toBeInTheDocument();

      await act(() => vi.advanceTimersByTimeAsync(BEAN_DROP_MS + 10));

      expect(beans()).toHaveLength(0);
      expect(screen.getByText("275")).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  test("does not play without an arrival, and plays once for each new one", async () => {
    setMediaQuery(REDUCED_MOTION_QUERY, false);
    vi.useFakeTimers();
    try {
      const { rerender } = render(<BeanDrop arrival={null}>240</BeanDrop>);
      expect(beans()).toHaveLength(0);

      rerender(<BeanDrop arrival={1}>275</BeanDrop>);
      expect(beans().length).toBeGreaterThan(0);
      await act(() => vi.advanceTimersByTimeAsync(BEAN_DROP_MS + 10));
      expect(beans()).toHaveLength(0);

      // The same arrival again (a re-render, a refetch): nothing plays twice.
      rerender(<BeanDrop arrival={1}>275</BeanDrop>);
      expect(beans()).toHaveLength(0);

      rerender(<BeanDrop arrival={2}>310</BeanDrop>);
      expect(beans().length).toBeGreaterThan(0);
    } finally {
      vi.useRealTimers();
    }
  });

  test("the bounce stands still with reduced motion: it is applied only where motion is welcome", () => {
    render(<BeanBounce />);

    const icon = document.querySelector("[data-slot=token-icon]")!;
    expect(icon.className).toContain("motion-safe:animate-bean-bounce");
    expect(icon.className).not.toMatch(/(^| )animate-bean-bounce/);
  });
});
