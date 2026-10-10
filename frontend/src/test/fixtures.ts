import type {
  AlertItem,
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

export const MARKET_ID = "3f6c2f0e-8b1d-4c57-9a43-0d6f1e2b7a10";
export const OTHER_MARKET_ID = "9a1b7c44-52de-4f0a-b6c3-7e85d1f0c2ab";
export const USER_ID = "7d3e5a90-1c2b-4f6d-8e7a-b9c0d1e2f3a4";
export const VOUCHER_ID = "c4a1e7d2-6b3f-4a58-9d0c-2e1f3a4b5c6d";
export const BATCH_ID = "1e2d3c4b-5a69-4788-8c9d-0e1f2a3b4c5d";

export function config(overrides: Partial<PublicConfigResponse> = {}): PublicConfigResponse {
  return {
    timezone: "Europe/Zurich",
    price_display_decimals: 1,
    quote_debounce_ms: 300,
    live_poll_fallback_seconds: 10,
    signup_grant_cr: "500",
    monthly_grant_cr: "200",
    max_stake_per_market_cr: "100",
    stake_presets_cr: ["25", "50", "100"],
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
    balance_cr: "240",
    can_get_voucher: true,
    monthly_grant_cr: "200",
    next_monthly_grant_on: "2026-11-01",
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

/** A viewer who has not bought into the market and may trade it. */
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
    stake_presets: [
      { amount_cr: "25", allowed: true },
      { amount_cr: "50", allowed: true },
      { amount_cr: "100", allowed: true },
    ],
    sell_presets: [
      { outcome: "YES", portion: "HALF", shares: "0", allowed: false },
      { outcome: "YES", portion: "ALL", shares: "0", allowed: false },
      { outcome: "NO", portion: "HALF", shares: "0", allowed: false },
      { outcome: "NO", portion: "ALL", shares: "0", allowed: false },
    ],
    ...overrides,
  };
}

/** A viewer who put 50 into YES: holds 91.645 shares and can sell half or all of them. */
export function marketViewerWithPosition(overrides: Partial<MarketViewer> = {}): MarketViewer {
  return marketViewer({
    balance_cr: "190",
    cost_cr: "50",
    net_stake_cr: "50",
    stake_left_cr: "50",
    holdings: holdings({ shares: "91.645", value_cr: "50.221461" }),
    stake_presets: [
      { amount_cr: "25", allowed: true },
      { amount_cr: "50", allowed: true },
      { amount_cr: "100", allowed: false },
    ],
    sell_presets: [
      { outcome: "YES", portion: "HALF", shares: "45.8225", allowed: true },
      { outcome: "YES", portion: "ALL", shares: "91.645", allowed: true },
      { outcome: "NO", portion: "HALF", shares: "0", allowed: false },
      { outcome: "NO", portion: "ALL", shares: "0", allowed: false },
    ],
    ...overrides,
  });
}

export function marketDetail(overrides: Partial<MarketDetailResponse> = {}): MarketDetailResponse {
  return {
    ...marketSummary(),
    yes_definition: "No interruption of more than 30 minutes is reported.",
    no_definition: "At least one interruption of more than 30 minutes is reported.",
    resolution_criteria: "Decided from the operator's published service notices for the session.",
    resolution_source: "polybahn.ch service notices",
    void_rule: "If the market is voided, every trader gets their net stake back.",
    requested_tier: "DEFAULT",
    tier: "DEFAULT",
    b_cr: "1000",
    max_loss_cr: "693.147181",
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

export function history(overrides: Partial<HistoryResponse> = {}): HistoryResponse {
  return {
    source: "postgres",
    points: [
      { t: "2026-10-01T12:00:00Z", p_yes: 0.5, version: 0 },
      { t: "2026-10-02T09:30:00Z", p_yes: 0.524, version: 3 },
      { t: "2026-10-03T17:45:00Z", p_yes: 0.548, version: 7 },
    ],
    ...overrides,
  };
}

/** A quote for buying YES with 25. */
export function quote(overrides: Partial<QuoteResponse> = {}): QuoteResponse {
  return {
    market_id: MARKET_ID,
    side: "BUY",
    outcome: "YES",
    amount_cr: "25",
    shares: "45.12",
    average_price: 0.554,
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
    code: "B4T8WN3C",
    expires_at: "2026-11-03T10:00:00Z",
    ...overrides,
  };
}

/** The answer of `POST /barista/vouchers/redeem`. */
export function voucherRedeemed(
  overrides: Partial<VoucherRedeemResponse> = {},
): VoucherRedeemResponse {
  return {
    voucher_id: VOUCHER_ID,
    product_id: "coffee",
    product_name: "Coffee",
    value_cr: "35",
    redeemed_at: "2026-10-05T08:02:00Z",
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
        degree_level: "MASTER",
        degree_programme: "MSc Data Science",
      }),
      viewer,
      leaderboardEntry({ rank: 4, username: "mensa_fan", credits_cr: "120.25", trades: 1 }),
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
    requested_tier: "DEFAULT",
    status: "PROPOSED",
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
    created_at: "2026-09-14T09:00:00Z",
    expires_at: "2026-12-31T22:59:00Z",
    revoked_at: null,
    ...overrides,
  };
}

/** The answer of `POST /admin/code-batches`: the one place the plain codes appear. */
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
