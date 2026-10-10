import {
  type InfiniteData,
  mutationOptions,
  type QueryClient,
  type QueryKey,
} from "@tanstack/react-query";

import { call } from "./client";
import type { ApiError } from "./errors";
import { keys } from "./keys";
import type {
  AdjustBalanceRequest,
  AdminUserItem,
  AdminUserListResponse,
  ApproveMarketRequest,
  CloseMarketRequest,
  CreateCodeBatchRequest,
  HaltMarketRequest,
  MarketDetailResponse,
  MeResponse,
  ProfileSavedResponse,
  ProposalItem,
  ProposalListResponse,
  ProposeMarketRequest,
  RejectMarketRequest,
  ResolveMarketRequest,
  ResumeMarketRequest,
  SaveProfileRequest,
  TradeRequest,
  UserRole,
  VoidMarketRequest,
} from "./schema";
import type { Serialised, Submission } from "./submissions";

/**
 * Every write of the product, each with what it does to the cache. This file is the
 * invalidation table: a screen passes one of these to `useSubmit` (`lib/api/submit.ts`,
 * which also stops a second submit) and adds only what is its own (a toast, closing a
 * sheet) in the callbacks of `mutate()`. The two writes that carry an Idempotency-Key, the
 * trade and the adjustment of a user's tokens, are sent by the store that keeps their
 * body and key (`lib/api/submissions.ts`).
 *
 * After its answer, a write sets what the answer holds and marks out of date what it
 * changed. After no answer (no response, or an error of the server: DESIGN.md §14,
 * "Other writes without an answer") it reads again what it would have changed.
 *
 * | Write                        | After its answer                    | After no answer        |
 * | ---------------------------- | ----------------------------------- | ---------------------- |
 * | `saveProfileMutation`        | `me` is the answer; leaderboard     | nothing: saving again  |
 * |                              |                                     | is safe                |
 * | `tradeMutation`              | balance and market from the answer; | nothing: the sheet     |
 * |                              | the market, its history, all under  | sends the same request |
 * |                              | `me`, market lists, leaderboard     | again                  |
 * | `redeemCodeMutation`         | balance from the answer; all under  | `me`, ledger           |
 * |                              | `me`, products, leaderboard         |                        |
 * | `issueVoucherMutation`       | balance from the answer; all under  | vouchers, `me`         |
 * |                              | `me`, products, leaderboard         |                        |
 * | `replaceVoucherCodeMutation` | nothing; vouchers when the voucher  | nothing: asking again  |
 * |                              | is used up or gone                  | is safe                |
 * | `redeemVoucherMutation`      | nothing                             | nothing can be read    |
 * | `proposeMarketMutation`      | the market is the answer, and the   | proposals              |
 * |                              | first of the proposals; proposals,  |                        |
 * |                              | market lists                        |                        |
 * | the seven market actions     | the market is the answer; its       | the market, market     |
 * |                              | history, market lists, risk, all    | lists, risk            |
 * |                              | under `me`, leaderboard             |                        |
 * | `createCodeBatchMutation`    | batches, risk                       | batches                |
 * | `revokeCodesMutation`        | batches, risk; the codes of the     | batches, risk          |
 * |                              | batch are forgotten                 |                        |
 * | `acknowledgeAlertMutation`   | risk                                | risk                   |
 * | `suspendUserMutation`,       | the user's row is the answer, in    | the lists of users     |
 * | `reinstateUserMutation`      | every list of users; the lists of   |                        |
 * |                              | users, leaderboard                  |                        |
 * | `setUserRoleMutation`        | the user's row is the answer, in    | the lists of users     |
 * |                              | every list of users; the lists of   |                        |
 * |                              | users                               |                        |
 * | `adjustUserBalanceMutation`  | the user's balance from the answer, | nothing: the dialog    |
 * |                              | in every list of users; risk,       | sends the same request |
 * |                              | leaderboard                         | again                  |
 *
 * "After no answer" is awaited before the mutation stops being pending. A button that is
 * disabled while its mutation is pending therefore stays disabled until the screen shows
 * what the server recorded, which is what DESIGN.md §14 asks for.
 */

/** Marks queries out of date; the ones on screen are asked again. Resolves when they answered. */
function refresh(client: QueryClient, ...queryKeys: readonly QueryKey[]): Promise<unknown> {
  return Promise.all(queryKeys.map((queryKey) => client.invalidateQueries({ queryKey })));
}

/** `GET /me` alone, without the lists under the same prefix. */
function refreshMe(client: QueryClient): Promise<unknown> {
  return client.invalidateQueries({ queryKey: keys.me, exact: true });
}

/** A balance the API just sent is the balance, before `/me` is asked again. */
function setBalance(client: QueryClient, balance_cr: string): void {
  client.setQueryData<MeResponse>(keys.me, (me) => (me === undefined ? me : { ...me, balance_cr }));
}

/**
 * What goes out for a stored body (DESIGN.md §14): the string as it was serialised on the
 * confirming tap. It is not built or serialised a second time.
 */
function stored<Request>(body: Serialised<Request>) {
  return {
    body: body as unknown as Request,
    bodySerializer: (serialised: Request) => serialised as unknown as string,
  };
}

/* ---- Profile ---------------------------------------------------------------------- */

/**
 * What a save of the profile states: the username and the degree. `policy_accepted: true`
 * goes with the save that creates the profile and with no other; a save that changes a
 * profile leaves the field out (DESIGN.md §13).
 */
export type ProfileSave = Omit<SaveProfileRequest, "policy_accepted"> & { policy_accepted?: true };

/** `PUT /me/profile`: creates the profile, or changes the username and the degree. */
export const saveProfileMutation = mutationOptions({
  mutationFn: (body: ProfileSave) =>
    // The contract lets `policy_accepted` be left out. The generated type lists it all the
    // same, because it has a default, which is how an answer would carry it.
    call((api) => api.PUT("/api/me/profile", { body: body as SaveProfileRequest })),
  onSuccess: (saved: ProfileSavedResponse, _body, _result, { client }) => {
    // `signup_granted` describes this save, not the profile; it stays out of the cache.
    const { signup_granted, ...me } = saved;
    client.setQueryData<MeResponse>(keys.me, me);
    void refresh(client, keys.leaderboard.all);
  },
});

/* ---- Trading ---------------------------------------------------------------------- */

/**
 * `POST /markets/{market_id}/trades` with the stored body and key. A trade goes out as a
 * `Submission` whose `target` is the market: the body serialised once and the one
 * Idempotency-Key that belongs to it, kept and sent again unchanged as
 * `lib/api/submissions.ts` says (DESIGN.md §14).
 */
export const tradeMutation = mutationOptions({
  mutationFn: ({ target, body, idempotencyKey }: Submission<TradeRequest>) =>
    call((api) =>
      api.POST("/api/markets/{market_id}/trades", {
        params: { path: { market_id: target }, header: { "Idempotency-Key": idempotencyKey } },
        ...stored(body),
      }),
    ),
  onSuccess: (trade, { target: marketId }, _result, { client }) => {
    setBalance(client, trade.balance_cr);
    client.setQueryData<MarketDetailResponse>(keys.markets.detail(marketId), (market) => {
      if (market === undefined) return market;
      const price =
        trade.market_version > market.version
          ? { p_yes: trade.p_yes_after, p_no: trade.p_no_after, version: trade.market_version }
          : {};
      return {
        ...market,
        ...price,
        viewer: {
          ...market.viewer,
          balance_cr: trade.balance_cr,
          net_stake_cr: trade.net_stake_cr,
          stake_left_cr: trade.stake_left_cr,
        },
      };
    });
    void refresh(
      client,
      keys.markets.detail(marketId),
      keys.markets.history(marketId),
      keys.markets.lists,
      keys.me,
      keys.leaderboard.all,
    );
  },
  onError: (error: ApiError, { target: marketId }, _result, { client }) => {
    // A refusal that means the screen is behind: show the market as it is now.
    switch (error.code) {
      case "INSUFFICIENT_BALANCE":
        void refreshMe(client);
        void refresh(client, keys.markets.detail(marketId));
        break;
      case "INSUFFICIENT_SHARES":
      case "MARKET_NOT_OPEN":
      case "LIMIT_EXCEEDED":
        void refresh(client, keys.markets.detail(marketId));
        break;
    }
  },
});

/* ---- Codes and vouchers ----------------------------------------------------------- */

/** `POST /codes/redeem`: the code alone, never a link. */
export const redeemCodeMutation = mutationOptions({
  mutationFn: (code: string) => call((api) => api.POST("/api/codes/redeem", { body: { code } })),
  onSuccess: (redeemed, _code, _result, { client }) => {
    setBalance(client, redeemed.balance_cr);
    void refresh(client, keys.me, keys.products, keys.leaderboard.all);
  },
  onError: (error: ApiError, _code, _result, { client }) => {
    if (error.outcomeUnknown) return Promise.all([refreshMe(client), refresh(client, keys.ledger)]);
  },
});

/** `POST /vouchers`: turns tokens into a voucher for one product. The answer holds its code. */
export const issueVoucherMutation = mutationOptions({
  mutationFn: (productId: string) =>
    call((api) => api.POST("/api/vouchers", { body: { product_id: productId } })),
  onSuccess: (voucher, _productId, _result, { client }) => {
    setBalance(client, voucher.balance_cr);
    void refresh(client, keys.me, keys.products, keys.leaderboard.all);
  },
  onError: (error: ApiError, _productId, _result, { client }) => {
    if (error.outcomeUnknown) {
      return Promise.all([refresh(client, keys.vouchers), refreshMe(client)]);
    }
    if (error.code === "INSUFFICIENT_BALANCE") {
      void refreshMe(client);
      void refresh(client, keys.products);
    }
  },
});

/** `POST /vouchers/{voucher_id}/code`: a fresh code; the one shown before stops working. */
export const replaceVoucherCodeMutation = mutationOptions({
  mutationFn: (voucherId: string) =>
    call((api) =>
      api.POST("/api/vouchers/{voucher_id}/code", { params: { path: { voucher_id: voucherId } } }),
    ),
  onError: (error: ApiError, _voucherId, _result, { client }) => {
    // Used up, expired or gone: the list behind the sheet is out of date.
    if (error.code === "CONFLICT" || error.code === "NOT_FOUND") {
      void refresh(client, keys.vouchers);
    }
  },
});

/** `POST /barista/vouchers/redeem`: the scanned or typed voucher code, in the body. */
export const redeemVoucherMutation = mutationOptions({
  mutationFn: (code: string) =>
    call((api) => api.POST("/api/barista/vouchers/redeem", { body: { code } })),
});

/* ---- Markets ---------------------------------------------------------------------- */

/** A market as its row of `GET /me/proposals`: the fields of the answer that list has. */
function asProposal(market: MarketDetailResponse): ProposalItem {
  return {
    id: market.id,
    question: market.question,
    yes_definition: market.yes_definition,
    no_definition: market.no_definition,
    resolution_criteria: market.resolution_criteria,
    resolution_source: market.resolution_source,
    close_at: market.close_at,
    resolve_by: market.resolve_by,
    status: market.status,
    outcome: market.outcome,
    rejection_reason: market.rejection_reason,
    created_at: market.created_at,
  };
}

/**
 * `POST /markets`: a proposal. The answer is the market, in status PROPOSED. It stands at
 * the top of the caller's proposals at once (DESIGN.md §13): the list is newest first,
 * and this is the newest. The list is read again all the same.
 */
export const proposeMarketMutation = mutationOptions({
  mutationFn: (body: ProposeMarketRequest) => call((api) => api.POST("/api/markets", { body })),
  onSuccess: (market, _body, _result, { client }) => {
    client.setQueryData<MarketDetailResponse>(keys.markets.detail(market.id), market);
    client.setQueryData<ProposalListResponse>(keys.proposals, (list) =>
      list === undefined
        ? list
        : {
            ...list,
            items: [asProposal(market), ...list.items.filter((item) => item.id !== market.id)],
          },
    );
    void refresh(client, keys.proposals, keys.markets.lists);
  },
  onError: (error: ApiError, _body, _result, { client }) => {
    if (error.outcomeUnknown) return refresh(client, keys.proposals);
  },
});

/** What every admin action on a market does to the cache: its answer is the market. */
const marketAction = {
  onSuccess: (
    market: MarketDetailResponse,
    _variables: unknown,
    _result: unknown,
    { client }: { client: QueryClient },
  ) => {
    client.setQueryData<MarketDetailResponse>(keys.markets.detail(market.id), market);
    void refresh(
      client,
      keys.markets.history(market.id),
      keys.markets.lists,
      keys.admin.risk,
      keys.me,
      keys.leaderboard.all,
    );
  },
  onError: (
    error: ApiError,
    { marketId }: { marketId: string },
    _result: unknown,
    { client }: { client: QueryClient },
  ) => {
    if (error.outcomeUnknown) {
      return refresh(client, keys.markets.detail(marketId), keys.markets.lists, keys.admin.risk);
    }
    // The market is not what the dialog thought it was: show it as it is.
    if (
      error.code === "CONFLICT" ||
      error.code === "LIMIT_EXCEEDED" ||
      error.code === "FORBIDDEN"
    ) {
      void refresh(client, keys.markets.detail(marketId), keys.admin.risk);
    }
  },
};

/** `POST /admin/markets/{market_id}/approve`. */
export const approveMarketMutation = mutationOptions({
  mutationFn: ({ marketId, body }: { marketId: string; body: ApproveMarketRequest }) =>
    call((api) =>
      api.POST("/api/admin/markets/{market_id}/approve", {
        params: { path: { market_id: marketId } },
        body,
      }),
    ),
  ...marketAction,
});

/** `POST /admin/markets/{market_id}/reject`. */
export const rejectMarketMutation = mutationOptions({
  mutationFn: ({ marketId, body }: { marketId: string; body: RejectMarketRequest }) =>
    call((api) =>
      api.POST("/api/admin/markets/{market_id}/reject", {
        params: { path: { market_id: marketId } },
        body,
      }),
    ),
  ...marketAction,
});

/** `POST /admin/markets/{market_id}/halt`. */
export const haltMarketMutation = mutationOptions({
  mutationFn: ({ marketId, body }: { marketId: string; body: HaltMarketRequest }) =>
    call((api) =>
      api.POST("/api/admin/markets/{market_id}/halt", {
        params: { path: { market_id: marketId } },
        body,
      }),
    ),
  ...marketAction,
});

/** `POST /admin/markets/{market_id}/resume`. */
export const resumeMarketMutation = mutationOptions({
  mutationFn: ({ marketId, body }: { marketId: string; body: ResumeMarketRequest }) =>
    call((api) =>
      api.POST("/api/admin/markets/{market_id}/resume", {
        params: { path: { market_id: marketId } },
        body,
      }),
    ),
  ...marketAction,
});

/** `POST /admin/markets/{market_id}/close`. */
export const closeMarketMutation = mutationOptions({
  mutationFn: ({ marketId, body }: { marketId: string; body: CloseMarketRequest }) =>
    call((api) =>
      api.POST("/api/admin/markets/{market_id}/close", {
        params: { path: { market_id: marketId } },
        body,
      }),
    ),
  ...marketAction,
});

/** `POST /admin/markets/{market_id}/resolve`. */
export const resolveMarketMutation = mutationOptions({
  mutationFn: ({ marketId, body }: { marketId: string; body: ResolveMarketRequest }) =>
    call((api) =>
      api.POST("/api/admin/markets/{market_id}/resolve", {
        params: { path: { market_id: marketId } },
        body,
      }),
    ),
  ...marketAction,
});

/** `POST /admin/markets/{market_id}/void`. */
export const voidMarketMutation = mutationOptions({
  mutationFn: ({ marketId, body }: { marketId: string; body: VoidMarketRequest }) =>
    call((api) =>
      api.POST("/api/admin/markets/{market_id}/void", {
        params: { path: { market_id: marketId } },
        body,
      }),
    ),
  ...marketAction,
});

/* ---- Admin: codes and risk -------------------------------------------------------- */

/**
 * `POST /admin/code-batches`. The answer carries the batch and its codes; `batchCodesQuery`
 * reads those of them that can be redeemed again.
 */
export const createCodeBatchMutation = mutationOptions({
  mutationFn: (body: CreateCodeBatchRequest) =>
    call((api) => api.POST("/api/admin/code-batches", { body })),
  onSuccess: (_created, _body, _result, { client }) => {
    void refresh(client, keys.admin.batches, keys.admin.risk);
  },
  onError: (error: ApiError, _body, _result, { client }) => {
    if (error.outcomeUnknown) return refresh(client, keys.admin.batches);
  },
});

/**
 * `POST /admin/code-batches/{batch_id}/revoke`. Without `codes` every code of the batch
 * that has not been redeemed stops working.
 */
export const revokeCodesMutation = mutationOptions({
  mutationFn: ({ batchId, codes }: { batchId: string; codes?: string[] }) =>
    call((api) =>
      api.POST("/api/admin/code-batches/{batch_id}/revoke", {
        params: { path: { batch_id: batchId } },
        body: codes === undefined ? {} : { codes },
      }),
    ),
  onSuccess: (_revoked, { batchId }, _result, { client }) => {
    // Codes that were read before the revocation are codes that work no more.
    client.removeQueries({ queryKey: keys.admin.batchCodes(batchId) });
    void refresh(client, keys.admin.batches, keys.admin.risk);
  },
  onError: (error: ApiError, _variables, _result, { client }) => {
    if (error.outcomeUnknown) return refresh(client, keys.admin.batches, keys.admin.risk);
  },
});

/** `POST /admin/alerts/{alert_id}/ack`. */
export const acknowledgeAlertMutation = mutationOptions({
  mutationFn: (alertId: number) =>
    call((api) =>
      api.POST("/api/admin/alerts/{alert_id}/ack", { params: { path: { alert_id: alertId } } }),
    ),
  onSuccess: (_alert, _alertId, _result, { client }) => {
    void refresh(client, keys.admin.risk);
  },
  onError: (error: ApiError, _alertId, _result, { client }) => {
    if (error.outcomeUnknown) return refresh(client, keys.admin.risk);
  },
});

/* ---- Admin: users ----------------------------------------------------------------- */

/** Puts what an answer says about one user into every list of users the cache holds. */
function setUser(
  client: QueryClient,
  userId: string,
  change: (user: AdminUserItem) => AdminUserItem,
): void {
  client.setQueriesData<InfiniteData<AdminUserListResponse>>(
    { queryKey: keys.admin.users.all },
    (list) =>
      list === undefined
        ? list
        : {
            ...list,
            pages: list.pages.map((page) => ({
              ...page,
              items: page.items.map((user) => (user.id === userId ? change(user) : user)),
            })),
          },
  );
}

/**
 * What suspending, reinstating and changing a role share: the answer is the user's row.
 * It is shown at once in every list, and the lists are read again, because a list that is
 * filtered by status or role may hold other rows from here on. After no answer the lists
 * are read before the mutation settles: the row then shows what the server holds.
 */
const userAction = {
  onError: (
    error: ApiError,
    _variables: unknown,
    _result: unknown,
    { client }: { client: QueryClient },
  ) => {
    if (error.outcomeUnknown) return refresh(client, keys.admin.users.all);
    // The row is not what the dialog thought it was, or it is gone: show the list as it is.
    if (error.code === "CONFLICT" || error.code === "NOT_FOUND") {
      void refresh(client, keys.admin.users.all);
    }
  },
};

/** `POST /admin/users/{user_id}/suspend`, with the reason for the audit log. */
export const suspendUserMutation = mutationOptions({
  mutationFn: ({ userId, reason }: { userId: string; reason: string }) =>
    call((api) =>
      api.POST("/api/admin/users/{user_id}/suspend", {
        params: { path: { user_id: userId } },
        body: { reason },
      }),
    ),
  onSuccess: (user, _variables, _result, { client }) => {
    setUser(client, user.id, () => user);
    // A suspended account is on no leaderboard.
    void refresh(client, keys.admin.users.all, keys.leaderboard.all);
  },
  ...userAction,
});

/** `POST /admin/users/{user_id}/reinstate`, with the reason for the audit log. */
export const reinstateUserMutation = mutationOptions({
  mutationFn: ({ userId, reason }: { userId: string; reason: string }) =>
    call((api) =>
      api.POST("/api/admin/users/{user_id}/reinstate", {
        params: { path: { user_id: userId } },
        body: { reason },
      }),
    ),
  onSuccess: (user, _variables, _result, { client }) => {
    setUser(client, user.id, () => user);
    void refresh(client, keys.admin.users.all, keys.leaderboard.all);
  },
  ...userAction,
});

/** `POST /admin/users/{user_id}/role`: USER, BARISTA or ADMIN. */
export const setUserRoleMutation = mutationOptions({
  mutationFn: ({ userId, role }: { userId: string; role: UserRole }) =>
    call((api) =>
      api.POST("/api/admin/users/{user_id}/role", {
        params: { path: { user_id: userId } },
        body: { role },
      }),
    ),
  onSuccess: (user, _variables, _result, { client }) => {
    setUser(client, user.id, () => user);
    void refresh(client, keys.admin.users.all);
  },
  ...userAction,
});

/**
 * `POST /admin/users/{user_id}/adjustments` with the stored body and key. An adjustment
 * goes out as a `Submission` whose `target` is the user, kept and sent again unchanged
 * exactly as a trade is (`lib/api/submissions.ts`, DESIGN.md §14). `amount_cr` is the
 * amount as it was typed, with a minus sign in front of it for a removal.
 */
export const adjustUserBalanceMutation = mutationOptions({
  mutationFn: ({ target, body, idempotencyKey }: Submission<AdjustBalanceRequest>) =>
    call((api) =>
      api.POST("/api/admin/users/{user_id}/adjustments", {
        params: { path: { user_id: target }, header: { "Idempotency-Key": idempotencyKey } },
        ...stored(body),
      }),
    ),
  onSuccess: (adjusted, _submission, _result, { client }) => {
    setUser(client, adjusted.user_id, (user) => ({ ...user, balance_cr: adjusted.balance_cr }));
    void refresh(client, keys.admin.risk, keys.leaderboard.all);
  },
  onError: (error: ApiError, _submission, _result, { client }) => {
    // The balance in the row is behind, or the row is gone: show the list as it is.
    if (error.code === "INSUFFICIENT_BALANCE" || error.code === "NOT_FOUND") {
      void refresh(client, keys.admin.users.all);
    }
  },
});
