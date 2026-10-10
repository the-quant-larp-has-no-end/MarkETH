import type {
  AdminUserItem,
  AdminUserListResponse,
  AlertItem,
  BalanceAdjustmentResponse,
  BuyEntry,
  CodeBatchCodesResponse,
  CodeBatchCreatedResponse,
  CodeBatchItem,
  HistoryResponse,
  Holding,
  LeaderboardEntry,
  LeaderboardResponse,
  LedgerItem,
  MarketDetailResponse,
  MarketSummary,
  MarketViewer,
  MeResponse,
  NoticeItem,
  PortfolioEntry,
  PortfolioResponse,
  ProfileSavedResponse,
  ProposalItem,
  PublicConfigResponse,
  QuoteEvent,
  QuoteResponse,
  RiskMarketRow,
  RiskResponse,
  StatusEvent,
  TradeItem,
  TradeResponse,
  VoucherCodeResponse,
  VoucherIssuedResponse,
  VoucherItem,
  VoucherProductItem,
  VoucherRedeemResponse,
} from "@/lib/api/schema";
import type { LiveEventOf } from "@/lib/live/events";

/**
 * API answers for tests, typed by the generated schema: when the contract changes, the
 * fixtures stop compiling before a test can lie. Each function returns a complete,
 * plausible answer and takes the fields a test cares about:
 *
 *     marketDetail({ status: "HALTED", trading_open: false })
 *     me({ role: "ADMIN" })
 *
 * Amounts are the strings the API sends: trailing zeros trimmed, a zero as `0`. A test
 * expects those strings on screen and never does arithmetic to find out what to expect.
 */

/**
 * The day the fixtures stand on: three days before their markets close and two months
 * before their proposal does. A screen that waits for a close time (`useAskAtClose`) sets
 * a timer for it, so a test that renders one fixes the device's clock to this day:
 *
 *     beforeEach(() => vi.useFakeTimers({ now: TODAY, toFake: ["Date"] }));
 *     afterEach(() => vi.useRealTimers());
 *
 * Run on another day, that timer would fire in the test.
 */
export const TODAY = new Date("2026-10-11T10:00:00Z");

export const MARKET_ID = "3f6c2f0e-8b1d-4c57-9a43-0d6f1e2b7a10";
export const OTHER_MARKET_ID = "9a1b7c44-52de-4f0a-b6c3-7e85d1f0c2ab";
export const USER_ID = "7d3e5a90-1c2b-4f6d-8e7a-b9c0d1e2f3a4";
export const VOUCHER_ID = "c4a1e7d2-6b3f-4a58-9d0c-2e1f3a4b5c6d";
export const BATCH_ID = "1e2d3c4b-5a69-4788-8c9d-0e1f2a3b4c5d";
export const OTHER_USER_ID = "b2f4c6d8-3e5a-4c7b-9d1f-a0b1c2d3e4f5";

export function config(overrides: Partial<PublicConfigResponse> = {}): PublicConfigResponse {
  return {
    timezone: "Europe/Zurich",
    price_display_decimals: 1,
    quote_debounce_ms: 300,
    live_poll_fallback_seconds: 10,
    live_heartbeat_seconds: 15,
    live_announce_spread_ms: 1000,
    live_viewer_refresh_seconds: 5,
    signup_grant_cr: "500",
    monthly_grant_cr: "200",
    max_stake_per_market_cr: "100",
    share_presets: ["10", "50", "100"],
    share_step: "1",
    stake_presets_cr: ["25", "50", "100"],
    stake_step_cr: "1",
    min_trade_cr: "1",
    min_trade_shares: "1",
    tiers: [
      { tier: "TIGHT", b_cr: "500", max_loss_cr: "346.573591" },
      { tier: "DEFAULT", b_cr: "1000", max_loss_cr: "693.147181" },
      { tier: "MEDIUM", b_cr: "2000", max_loss_cr: "1386.294362" },
    ],
    max_market_budget_cr: "5000",
    max_market_lifetime_days: 90,
    max_resolution_delay_days: 14,
    max_proposals_per_day: 3,
    question_min_length: 10,
    question_max_length: 200,
    short_text_max_length: 500,
    long_text_max_length: 2000,
    username_min_length: 3,
    username_max_length: 20,
    code_length: 12,
    code_min_value_cr: "5",
    code_max_value_cr: "500",
    code_default_value_cr: "35",
    code_batch_max_count: 500,
    code_label_max_length: 100,
    max_code_issuance_per_month_cr: "7000",
    voucher_code_length: 8,
    voucher_expiry_days: 30,
    degrees: [
      {
        level: "BACHELOR",
        name: "Bachelor",
        programmes: ["BSc Computer Science", "BSc Mathematics", "BSc Physics"],
      },
      {
        level: "MASTER",
        name: "Master",
        programmes: ["MSc Computer Science", "MSc Data Science"],
      },
      { level: "DOCTORATE", name: "Doctorate", programmes: ["Doctorate Computer Science"] },
      { level: "OTHER", name: "Other", programmes: ["Exchange student", "ETH staff", "Other"] },
    ],
    ...overrides,
  };
}

export function me(overrides: Partial<MeResponse> = {}): MeResponse {
  return {
    id: USER_ID,
    email: "anna.muster@ethz.ch",
    name: "Anna Muster",
    username: "anna_m",
    degree_level: "BACHELOR",
    degree_programme: "BSc Computer Science",
    role: "USER",
    created_at: "2026-09-21T08:15:00Z",
    policy_accepted_at: "2026-09-21T08:15:00Z",
    balance_cr: "240",
    can_get_voucher: true,
    monthly_grant_cr: "200",
    next_monthly_grant_at: "2026-10-21T08:15:00Z",
    notices: [],
    ...overrides,
  };
}

/** A notice of `/me`: VIS approved the market the viewer proposed, and it is open. */
export function notice(overrides: Partial<NoticeItem> = {}): NoticeItem {
  return {
    id: "0e6d4a52-7c1b-4f39-a2d8-5b9c3e1f7a60",
    kind: "PROPOSAL_APPROVED",
    market_id: MARKET_ID,
    question: "Will the Polybahn run without interruption during the exam session?",
    created_at: "2026-10-05T09:00:00Z",
    ...overrides,
  };
}

/** The answer of `PUT /me/profile`. `signup_granted` is true for a profile that was just created. */
export function profileSaved(overrides: Partial<ProfileSavedResponse> = {}): ProfileSavedResponse {
  return { ...me({ balance_cr: "500" }), signup_granted: true, ...overrides };
}

export function marketSummary(overrides: Partial<MarketSummary> = {}): MarketSummary {
  return {
    id: MARKET_ID,
    question: "Will the Polybahn run without interruption during the exam session?",
    status: "OPEN",
    trading_open: true,
    outcome: null,
    p_yes: 0.548,
    p_no: 0.452,
    volume_cr: "1250.5",
    close_at: "2026-10-14T16:00:00Z",
    resolve_by: "2026-10-21T16:00:00Z",
    version: 7,
    ...overrides,
  };
}

/** Always two entries, YES then NO, as the API sends them. */
export function holdings(yes: Partial<Holding> = {}, no: Partial<Holding> = {}): Holding[] {
  return [
    { outcome: "YES", shares: "0", value_cr: "0", ...yes },
    { outcome: "NO", shares: "0", value_cr: "0", ...no },
  ];
}

/**
 * What the viewer can buy, as the API sends it: always two entries, YES then NO, each with
 * the slider of its outcome and one shortcut per entry of `share_presets` of `config()`.
 * Without arguments every shortcut is allowed and the sliders run as far as a stake of 100
 * reaches at the price of `marketSummary()`:
 *
 *     buyEntries({ slider: null, presets: [] })     // nothing of YES can be bought
 */
export function buyEntries(yes: Partial<BuyEntry> = {}, no: Partial<BuyEntry> = {}): BuyEntry[] {
  const presets = () => [
    { shares: "10", allowed: true },
    { shares: "50", allowed: true },
    { shares: "100", allowed: true },
  ];
  return [
    { outcome: "YES", slider: { min: 1, max: 175, step: 1 }, presets: presets(), ...yes },
    { outcome: "NO", slider: { min: 1, max: 209, step: 1 }, presets: presets(), ...no },
  ];
}

/**
 * A viewer who has not bought into the market and may trade it. The slider of a buy runs
 * up to what the stake limit leaves, which is less than the balance here.
 */
export function marketViewer(overrides: Partial<MarketViewer> = {}): MarketViewer {
  return {
    balance_cr: "240",
    can_trade: true,
    cannot_trade_reason: null,
    can_settle: false,
    cost_cr: "0",
    proceeds_cr: "0",
    net_stake_cr: "0",
    stake_left_cr: "100",
    stake_limit_cr: "100",
    payout_cr: null,
    refund_cr: null,
    holdings: holdings(),
    buy: buyEntries(),
    stake_presets: [
      { amount_cr: "25", allowed: true },
      { amount_cr: "50", allowed: true },
      { amount_cr: "100", allowed: true },
    ],
    buy_slider: { min: 1, max: 100, step: 1 },
    sell_presets: [
      { outcome: "YES", portion: "HALF", shares: "0", proceeds_cr: "0", allowed: false },
      { outcome: "YES", portion: "ALL", shares: "0", proceeds_cr: "0", allowed: false },
      { outcome: "NO", portion: "HALF", shares: "0", proceeds_cr: "0", allowed: false },
      { outcome: "NO", portion: "ALL", shares: "0", proceeds_cr: "0", allowed: false },
    ],
    ...overrides,
  };
}

/**
 * A viewer who put 50 into YES: holds 91.645 shares and can sell half of them for
 * 24.850327 or all of them for 49.17859. The 50 that the stake limit leaves buy up to 89
 * YES shares or 107 NO shares, so the shortcut of 100 is allowed for NO alone.
 */
export function marketViewerWithPosition(overrides: Partial<MarketViewer> = {}): MarketViewer {
  return marketViewer({
    balance_cr: "190",
    cost_cr: "50",
    net_stake_cr: "50",
    stake_left_cr: "50",
    holdings: holdings({ shares: "91.645", value_cr: "50.221461" }),
    buy: buyEntries(
      {
        slider: { min: 1, max: 89, step: 1 },
        presets: [
          { shares: "10", allowed: true },
          { shares: "50", allowed: true },
          { shares: "100", allowed: false },
        ],
      },
      { slider: { min: 1, max: 107, step: 1 } },
    ),
    stake_presets: [
      { amount_cr: "25", allowed: true },
      { amount_cr: "50", allowed: true },
      { amount_cr: "100", allowed: false },
    ],
    buy_slider: { min: 1, max: 50, step: 1 },
    sell_presets: [
      {
        outcome: "YES",
        portion: "HALF",
        shares: "45.8225",
        proceeds_cr: "24.850327",
        allowed: true,
      },
      { outcome: "YES", portion: "ALL", shares: "91.645", proceeds_cr: "49.17859", allowed: true },
      { outcome: "NO", portion: "HALF", shares: "0", proceeds_cr: "0", allowed: false },
      { outcome: "NO", portion: "ALL", shares: "0", proceeds_cr: "0", allowed: false },
    ],
    ...overrides,
  });
}

/**
 * A market as a player reads it: how it is priced (`tier`, `b_cr`, `max_loss_cr`), what a
 * void would cost and who proposed it are sent to admins only, so all five are null here.
 * `adminMarketDetail` is the same market as an admin reads it.
 */
export function marketDetail(overrides: Partial<MarketDetailResponse> = {}): MarketDetailResponse {
  return {
    ...marketSummary(),
    yes_definition: "No interruption of more than 30 minutes is reported.",
    no_definition: "At least one interruption of more than 30 minutes is reported.",
    resolution_criteria: "Decided from the operator's published service notices for the session.",
    resolution_source: "polybahn.ch service notices",
    void_rule: "If the market is voided, every trader gets their net stake back.",
    tier: null,
    b_cr: null,
    max_loss_cr: null,
    void_cost_cr: null,
    proposer_email: null,
    rejection_reason: null,
    resolution: null,
    created_at: "2026-10-01T09:00:00Z",
    opened_at: "2026-10-01T12:00:00Z",
    closed_at: null,
    viewer: marketViewer(),
    ...overrides,
  };
}

/**
 * The same market as an admin who did not trade it reads it: with its proposer, its tier
 * and the two figures of that tier, what a void would cost VIS right now, and free to
 * settle it.
 */
export function adminMarketDetail(
  overrides: Partial<MarketDetailResponse> = {},
): MarketDetailResponse {
  return marketDetail({
    tier: "DEFAULT",
    b_cr: "1000",
    max_loss_cr: "693.147181",
    void_cost_cr: "-130.752819",
    proposer_email: "bea.beispiel@ethz.ch",
    viewer: marketViewer({ can_settle: true }),
    ...overrides,
  });
}

/**
 * The chart of a market that can still trade: three points, and `until`, the server's
 * now, which is the right edge of the chart.
 */
export function history(overrides: Partial<HistoryResponse> = {}): HistoryResponse {
  return {
    source: "postgres",
    points: [
      { t: "2026-10-01T12:00:00Z", p_yes: 0.5, version: 0 },
      { t: "2026-10-02T09:30:00Z", p_yes: 0.524, version: 3 },
      { t: "2026-10-03T17:45:00Z", p_yes: 0.548, version: 7 },
    ],
    until: "2026-10-04T10:00:00Z",
    ...overrides,
  };
}

/** A quote for buying 45.12 YES shares: they cost 25 and pay 45.12 if YES comes true. */
export function quote(overrides: Partial<QuoteResponse> = {}): QuoteResponse {
  return {
    market_id: MARKET_ID,
    side: "BUY",
    outcome: "YES",
    amount_cr: "25",
    shares: "45.12",
    average_price: 0.554,
    payout_cr: "45.12",
    p_yes_before: 0.548,
    p_yes_after: 0.559,
    p_no_before: 0.452,
    p_no_after: 0.441,
    quote_version: 7,
    ...overrides,
  };
}

export function tradeResponse(overrides: Partial<TradeResponse> = {}): TradeResponse {
  return {
    trade_id: "5b8f0a1c-2d3e-4f50-a1b2-c3d4e5f60718",
    market_id: MARKET_ID,
    side: "BUY",
    outcome: "YES",
    amount_cr: "25",
    shares: "45.123456",
    average_price: 0.554,
    p_yes_before: 0.548,
    p_yes_after: 0.559,
    p_no_before: 0.452,
    p_no_after: 0.441,
    market_version: 8,
    balance_cr: "215",
    net_stake_cr: "25",
    stake_left_cr: "75",
    created_at: "2026-10-04T10:00:00Z",
    ...overrides,
  };
}

export function tradeItem(overrides: Partial<TradeItem> = {}): TradeItem {
  return {
    id: "5b8f0a1c-2d3e-4f50-a1b2-c3d4e5f60718",
    market_id: MARKET_ID,
    market_question: "Will the Polybahn run without interruption during the exam session?",
    side: "BUY",
    outcome: "YES",
    amount_cr: "25",
    shares: "45.123456",
    average_price: 0.554,
    p_yes_before: 0.548,
    p_yes_after: 0.559,
    market_version: 8,
    created_at: "2026-10-04T10:00:00Z",
    ...overrides,
  };
}

export function portfolioEntry(overrides: Partial<PortfolioEntry> = {}): PortfolioEntry {
  return {
    market: marketSummary(),
    holdings: holdings({ shares: "91.645", value_cr: "50.221461" }),
    value_cr: "50.221461",
    cost_cr: "50",
    proceeds_cr: "0",
    net_stake_cr: "50",
    payout_cr: null,
    refund_cr: null,
    ...overrides,
  };
}

export function portfolio(overrides: Partial<PortfolioResponse> = {}): PortfolioResponse {
  return { total_value_cr: "50.221461", items: [portfolioEntry()], ...overrides };
}

export function ledgerItem(overrides: Partial<LedgerItem> = {}): LedgerItem {
  return {
    id: 41,
    kind: "MONTHLY_GRANT",
    description: "Monthly tokens",
    amount_cr: "200",
    created_at: "2026-10-01T00:00:05Z",
    ...overrides,
  };
}

export function voucherProduct(overrides: Partial<VoucherProductItem> = {}): VoucherProductItem {
  return { id: "coffee", name: "Coffee", price_cr: "35", affordable: true, ...overrides };
}

export function voucherItem(overrides: Partial<VoucherItem> = {}): VoucherItem {
  return {
    id: VOUCHER_ID,
    product_id: "coffee",
    product_name: "Coffee",
    value_cr: "35",
    status: "ACTIVE",
    refunded: false,
    created_at: "2026-10-04T10:00:00Z",
    expires_at: "2026-11-03T10:00:00Z",
    redeemed_at: null,
    ...overrides,
  };
}

/** The answer of `POST /vouchers`: the voucher, its code and the balance after it. */
export function voucherIssued(
  overrides: Partial<VoucherIssuedResponse> = {},
): VoucherIssuedResponse {
  return { ...voucherItem(), code: "7KQ2M9XA", balance_cr: "205", ...overrides };
}

export function voucherCode(overrides: Partial<VoucherCodeResponse> = {}): VoucherCodeResponse {
  return {
    voucher_id: VOUCHER_ID,
    product_name: "Coffee",
    code: "B4T8WN3C",
    expires_at: "2026-11-03T10:00:00Z",
    ...overrides,
  };
}

/**
 * The answer of `POST /barista/vouchers/redeem` to the scan that redeemed the voucher.
 * With `repeat: true` it is the answer to the same barista scanning it again a moment
 * later.
 */
export function voucherRedeemed(
  overrides: Partial<VoucherRedeemResponse> = {},
): VoucherRedeemResponse {
  return {
    voucher_id: VOUCHER_ID,
    product_id: "coffee",
    product_name: "Coffee",
    value_cr: "35",
    redeemed_at: "2026-10-05T08:02:00Z",
    repeat: false,
    ...overrides,
  };
}

export function leaderboardEntry(overrides: Partial<LeaderboardEntry> = {}): LeaderboardEntry {
  return {
    rank: 1,
    username: "anna_m",
    degree_level: "BACHELOR",
    degree_programme: "BSc Computer Science",
    credits_cr: "1204.5",
    balance_cr: "1000",
    positions_value_cr: "204.5",
    trades: 37,
    settled_markets: 8,
    win_rate: 0.625,
    avg_payout_ratio: 1.35,
    is_viewer: false,
    ...overrides,
  };
}

/** Four ranked players; the viewer is third. */
export function leaderboard(overrides: Partial<LeaderboardResponse> = {}): LeaderboardResponse {
  const viewer = leaderboardEntry({
    rank: 3,
    username: "anna_m",
    credits_cr: "240",
    balance_cr: "200",
    positions_value_cr: "40",
    trades: 4,
    settled_markets: 0,
    win_rate: null,
    avg_payout_ratio: null,
    is_viewer: true,
  });
  return {
    items: [
      leaderboardEntry({ rank: 1, username: "polyterrasse" }),
      leaderboardEntry({
        rank: 2,
        username: "cab_coffee",
        credits_cr: "980",
        balance_cr: "980",
        positions_value_cr: "0",
        degree_level: "MASTER",
        degree_programme: "MSc Data Science",
      }),
      viewer,
      leaderboardEntry({
        rank: 4,
        username: "mensa_fan",
        credits_cr: "120.25",
        balance_cr: "100",
        positions_value_cr: "20.25",
        trades: 1,
      }),
    ],
    next_cursor: null,
    viewer,
    ...overrides,
  };
}

export function proposal(overrides: Partial<ProposalItem> = {}): ProposalItem {
  return {
    id: OTHER_MARKET_ID,
    question: "Will the ASVZ open a second climbing wall before the end of the semester?",
    yes_definition: "The ASVZ announces the opening on its website.",
    no_definition: "No opening is announced by the end of the semester.",
    resolution_criteria: "Decided from the ASVZ news page on the last day of the semester.",
    resolution_source: "asvz.ch news",
    close_at: "2026-12-11T17:00:00Z",
    resolve_by: "2026-12-18T17:00:00Z",
    status: "PROPOSED",
    outcome: null,
    rejection_reason: null,
    created_at: "2026-10-04T10:00:00Z",
    ...overrides,
  };
}

export function codeBatch(overrides: Partial<CodeBatchItem> = {}): CodeBatchItem {
  return {
    id: BATCH_ID,
    label: "Welcome week",
    value_cr: "35",
    total_value_cr: "700",
    count: 20,
    active_count: 14,
    redeemed_count: 6,
    revoked_count: 0,
    one_per_user: true,
    expired: false,
    can_revoke: true,
    created_at: "2026-09-14T09:00:00Z",
    expires_at: "2026-12-31T22:59:00Z",
    revoked_at: null,
    ...overrides,
  };
}

/** The answer of `POST /admin/code-batches`: the batch, and every code of it. */
export function codeBatchCreated(
  overrides: Partial<CodeBatchCreatedResponse> = {},
): CodeBatchCreatedResponse {
  return {
    batch: codeBatch({ count: 2, active_count: 2, redeemed_count: 0, total_value_cr: "70" }),
    codes: [
      { code: "4F7KQ2M9XAB3", redeem_url: "http://localhost:8080/wallet?code=4F7KQ2M9XAB3" },
      { code: "T8WN3CB4Z6PD", redeem_url: "http://localhost:8080/wallet?code=T8WN3CB4Z6PD" },
    ],
    ...overrides,
  };
}

/**
 * The answer of `GET /admin/code-batches/{batch_id}/codes`: the batch, and the codes of
 * it that can be redeemed. Here two of its twenty are left.
 */
export function codeBatchCodes(
  overrides: Partial<CodeBatchCodesResponse> = {},
): CodeBatchCodesResponse {
  return {
    batch: codeBatch({ active_count: 2, redeemed_count: 18 }),
    codes: [
      { code: "4F7KQ2M9XAB3", redeem_url: "http://localhost:8080/wallet?code=4F7KQ2M9XAB3" },
      { code: "T8WN3CB4Z6PD", redeem_url: "http://localhost:8080/wallet?code=T8WN3CB4Z6PD" },
    ],
    ...overrides,
  };
}

/** A row of `GET /admin/users`: a player an admin may suspend, appoint and adjust. */
export function adminUser(overrides: Partial<AdminUserItem> = {}): AdminUserItem {
  return {
    id: OTHER_USER_ID,
    email: "bea.beispiel@ethz.ch",
    name: "Bea Beispiel",
    username: "cab_coffee",
    degree_level: "MASTER",
    degree_programme: "MSc Data Science",
    role: "USER",
    status: "ACTIVE",
    bootstrap_admin: false,
    balance_cr: "980",
    created_at: "2026-09-22T10:30:00Z",
    ...overrides,
  };
}

/**
 * One page of `GET /admin/users`, newest profile first: a player, and the admin who is
 * looking, who is named in the server's configuration.
 */
export function adminUsers(overrides: Partial<AdminUserListResponse> = {}): AdminUserListResponse {
  return {
    items: [
      adminUser(),
      adminUser({
        id: USER_ID,
        email: "anna.muster@ethz.ch",
        name: "Anna Muster",
        username: "anna_m",
        degree_level: "BACHELOR",
        degree_programme: "BSc Computer Science",
        role: "ADMIN",
        bootstrap_admin: true,
        balance_cr: "240",
        created_at: "2026-09-21T08:15:00Z",
      }),
    ],
    next_cursor: null,
    ...overrides,
  };
}

/** The answer of `POST /admin/users/{user_id}/adjustments`: 35 tokens added to 980. */
export function balanceAdjusted(
  overrides: Partial<BalanceAdjustmentResponse> = {},
): BalanceAdjustmentResponse {
  return {
    user_id: OTHER_USER_ID,
    amount_cr: "35",
    balance_cr: "1015",
    created_at: "2026-10-05T09:00:00Z",
    ...overrides,
  };
}

export function alert(overrides: Partial<AlertItem> = {}): AlertItem {
  return {
    id: 3,
    severity: "WARNING",
    message: "Obligations are above the alert threshold.",
    context: { total_cr: "100250", alert_threshold_cr: "100000" },
    created_at: "2026-10-04T11:00:00Z",
    acked_at: null,
    ...overrides,
  };
}

export function riskMarketRow(overrides: Partial<RiskMarketRow> = {}): RiskMarketRow {
  return {
    id: MARKET_ID,
    question: "Will the Polybahn run without interruption during the exam session?",
    status: "OPEN",
    tier: "DEFAULT",
    p_yes: 0.548,
    volume_cr: "1250.5",
    q_yes: "412.5",
    q_no: "220.25",
    b_cr: "1000",
    budget_cr: "693.147181",
    account_balance_cr: "823.9",
    exposure_cr: "-411.4",
    void_cost_cr: "-130.752819",
    close_at: "2026-10-14T16:00:00Z",
    ...overrides,
  };
}

export function risk(overrides: Partial<RiskResponse> = {}): RiskResponse {
  return {
    obligations: {
      total_cr: "48211.4",
      alert_threshold_cr: "100000",
      above_threshold: false,
      user_balances_cr: "46300",
      pending_vouchers_cr: "1500",
      market_exposure_cr: "411.4",
    },
    loss_budget_cr: "4158.883086",
    max_loss_budget_cr: "70000",
    open_markets: 6,
    max_open_markets: 100,
    unredeemed_code_value_cr: "490",
    code_issuance_this_month_cr: "700",
    max_code_issuance_per_month_cr: "7000",
    adjustments_this_month_cr: "-12.5",
    alerts: [],
    markets: [riskMarketRow()],
    ...overrides,
  };
}

/** `event: quote` of the market stream. */
export function quoteEvent(overrides: Partial<QuoteEvent> = {}): QuoteEvent {
  return {
    type: "quote",
    market_id: MARKET_ID,
    p_yes: 0.559,
    p_no: 0.441,
    volume_cr: "1275.5",
    version: 8,
    ts: "2026-10-04T10:00:00Z",
    ...overrides,
  };
}

/** `event: status` of the market stream. */
export function statusEvent(overrides: Partial<StatusEvent> = {}): StatusEvent {
  return {
    type: "status",
    market_id: MARKET_ID,
    status: "HALTED",
    trading_open: false,
    ts: "2026-10-04T10:05:00Z",
    ...overrides,
  };
}

/*
 * The events of the event stream (`GET /stream`), each under the name it travels by. A
 * test plays one with `streams.latest().send(marketQuoteEvent({ version: 9 }))`.
 */

/** `ready`: the last of what a stream gets when it opens. */
export function readyEvent(overrides: Partial<LiveEventOf<"ready">> = {}): LiveEventOf<"ready"> {
  return { type: "ready", ts: "2026-10-04T10:00:00Z", ...overrides };
}

/** `beat`: the stream is alive. */
export function beatEvent(overrides: Partial<LiveEventOf<"beat">> = {}): LiveEventOf<"beat"> {
  return { type: "beat", ts: "2026-10-04T10:00:15Z", ...overrides };
}

/** `market.quote`: a trade moved the market of `marketSummary()` on to version 8. */
export function marketQuoteEvent(
  overrides: Partial<LiveEventOf<"market.quote">> = {},
): LiveEventOf<"market.quote"> {
  return {
    type: "market.quote",
    market_id: MARKET_ID,
    version: 8,
    p_yes: 0.559,
    p_no: 0.441,
    volume_cr: "1275.5",
    ts: "2026-10-04T10:00:00Z",
    ...overrides,
  };
}

/** `market.status`: an admin halted the market of `marketSummary()`. */
export function marketStatusEvent(
  overrides: Partial<LiveEventOf<"market.status">> = {},
): LiveEventOf<"market.status"> {
  return {
    type: "market.status",
    market_id: MARKET_ID,
    status: "HALTED",
    trading_open: false,
    ts: "2026-10-04T10:05:00Z",
    ...overrides,
  };
}

/** `leaderboard.changed`: the ranking moved. It carries no figure. */
export function leaderboardChangedEvent(
  overrides: Partial<LiveEventOf<"leaderboard.changed">> = {},
): LiveEventOf<"leaderboard.changed"> {
  return { type: "leaderboard.changed", ts: "2026-10-04T10:00:05Z", ...overrides };
}

/** `me.changed`: something of the caller's own changed. It carries no figure. */
export function meChangedEvent(
  overrides: Partial<LiveEventOf<"me.changed">> = {},
): LiveEventOf<"me.changed"> {
  return { type: "me.changed", ts: "2026-10-04T10:00:00Z", ...overrides };
}
