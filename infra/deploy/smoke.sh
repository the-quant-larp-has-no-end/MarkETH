#!/bin/sh
# Walk a running MarkETH stack from profile set-up to a redeemed voucher, through the
# proxy, as three callers named by forged identity headers.
#
#   sh infra/deploy/smoke.sh        on the machine the stack runs on, from its tree
#
# IT WRITES DATA. It registers two made-up people and the first address of ADMIN_EMAILS,
# and leaves a resolved market, trades and a redeemed voucher behind. Run it on an empty
# database and delete that database afterwards: on the VM `make deploy-smoke
# CONFIRM=smoke`, then `make deploy-wipe CONFIRM=wipe`. It refuses to start when its
# trader has a profile already.
#
# Each step calls the operations named beside it and needs the services behind them. An
# operation whose service is a stub answers 501; the step then fails and says which
# call it was. Steps run in this order and the walk stops at the first that fails.
#
#   1. Profile set-up     GET /api/config, GET /api/me, PUT /api/me/profile
#                         EmailPolicy, IdentityService, ProfileService, GrantService,
#                         AccountService
#   2. A market           POST /api/markets, POST /api/admin/markets/{id}/approve
#                         MarketService, MarketLifecycleService
#   3. A trade            GET /api/markets/{id}/quote, POST /api/markets/{id}/trades
#                         QuoteService, TradingService
#   4. The event stream   GET /api/markets/{id}/stream
#                         LiveQuoteService
#   5. A resolution       POST /api/admin/markets/{id}/close and .../resolve
#                         MarketLifecycleService
#   6. A voucher          GET /api/vouchers/products, POST /api/vouchers,
#                         POST /api/vouchers/{id}/code,
#                         POST /api/barista/vouchers/redeem, GET /api/me/vouchers
#                         VoucherService
#   7. The leaderboard    GET /api/leaderboard
#                         LeaderboardService
#   8. The books          every account of the ledger sums to zero (SPEC §1)
#
# Expected amounts are taken from the API's own answers (the grant from /api/config, a
# balance from the trade that changed it), never from numbers written here.
#
# Needs curl, jq, python3 and docker.

# shellcheck disable=SC2016  # a "$name" in single quotes is jq's or the container's
set -eu

REQUEST_SECONDS=15
STREAM_SECONDS=5

STEP="start"

fail() {
    echo "FAILED  step $STEP: $*" >&2
    exit 1
}

passed() {
    echo "ok      $*"
}

setting() {
    value=$(sed -n "s/^$1=//p" .env)
    case $value in
        \"*\")
            value=${value#\"}
            value=${value%\"}
            ;;
    esac
    printf '%s' "$value"
}

# call <who> <method> <path> [<json body>]
#
# <who> is trader, maker, admin or nobody. STATUS holds the HTTP status, $WORK/body the
# body. IDEMPOTENCY_KEY, when set, is sent along.
call() {
    who=$1
    method=$2
    path=$3
    json=${4-}
    case $who in
        trader)
            id=$TRADER_ID
            name='Zo%C3%AB%20Smoke'
            ;;
        maker)
            id=$MAKER_ID
            name='Max%20Smoke'
            ;;
        admin)
            id=$ADMIN_ID
            name='Ada%20Smoke'
            ;;
        nobody)
            id=
            name=
            ;;
        *) fail "no such caller: $who" ;;
    esac
    CALL="$method $path as $who"
    set -- --request "$method" --header 'Accept: application/json' --header "Origin: $ORIGIN"
    if [ -n "$id" ]; then
        set -- "$@" --header "X-User-Id: $id" --header "X-User-Name: $name"
    fi
    if [ -n "$json" ]; then
        set -- "$@" --header 'Content-Type: application/json' --data "$json"
    fi
    if [ -n "${IDEMPOTENCY_KEY-}" ]; then
        set -- "$@" --header "Idempotency-Key: $IDEMPOTENCY_KEY"
    fi
    STATUS=$(curl --silent --show-error --max-time "$REQUEST_SECONDS" --output "$WORK/body" \
        --write-out '%{http_code}' "$@" "$BASE$path") || fail "$CALL: no answer"
}

body_excerpt() {
    head -c 400 "$WORK/body" 2> /dev/null || true
}

# expect <status> [<error code>]: the last call answered with exactly that.
expect() {
    if [ "$STATUS" = "501" ] && [ "$1" != "501" ]; then
        fail "$CALL: 501, the service behind this operation is a stub. $(body_excerpt)"
    fi
    if [ "$STATUS" != "$1" ]; then
        fail "$CALL: expected HTTP $1, got $STATUS $(body_excerpt)"
    fi
    if [ -n "${2-}" ] && [ "$(jq -r '.code? // empty' "$WORK/body")" != "$2" ]; then
        fail "$CALL: expected the error code $2, got $(body_excerpt)"
    fi
}

# field <jq filter>: a value of the last body; fails when it is absent.
field() {
    jq -er "$1 | select(. != null)" "$WORK/body" 2> /dev/null \
        || fail "$CALL: the answer has no $1: $(body_excerpt)"
}

# holds <what it means otherwise> <jq arguments>: the last body satisfies the condition.
holds() {
    otherwise=$1
    shift
    jq -e "$@" "$WORK/body" > /dev/null 2>&1 || fail "$CALL: $otherwise. Got $(body_excerpt)"
}

new_key() {
    python3 -c 'import uuid; print(uuid.uuid4())'
}

days_from_now() {
    python3 -c 'import sys
from datetime import UTC, datetime, timedelta
print((datetime.now(UTC) + timedelta(days=int(sys.argv[1]))).strftime("%Y-%m-%dT%H:%M:%SZ"))' "$1"
}

# same_amount <a> <b>: two decimal strings name the same amount.
same_amount() {
    python3 -c 'import sys
from decimal import Decimal
sys.exit(0 if Decimal(sys.argv[1]) == Decimal(sys.argv[2]) else 1)' "$1" "$2"
}

sum_of() {
    python3 -c 'import sys
from decimal import Decimal
print(Decimal(sys.argv[1]) + Decimal(sys.argv[2]))' "$1" "$2"
}

profile_of() {
    jq -cn --arg username "$1" --arg level "$LEVEL" --arg programme "$PROGRAMME" \
        '{username: $username, degree_level: $level, degree_programme: $programme}'
}

# The body a barista's scan sends: the code travels in no address.
scan_of() {
    jq -cn --arg code "$1" '{code: $code}'
}

cd "$(dirname "$0")/../.."
[ -f .env ] || fail "no .env in $(pwd)"
for tool in curl jq python3 docker; do
    command -v "$tool" > /dev/null || fail "$tool is not installed"
done

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

BIND=$(setting NGINX_HTTP_BIND)
PORT=$(setting NGINX_HTTP_PORT)
# The two made-up people are ids at the first allowed domain.
DOMAIN=$(setting ALLOWED_EMAIL_DOMAINS | cut -d, -f1)
ORIGIN=$(setting PUBLIC_BASE_URL)
ADMIN_ID=$(setting ADMIN_EMAILS | cut -d, -f1)
[ -n "$PORT" ] || fail ".env has no NGINX_HTTP_PORT"
[ -n "$DOMAIN" ] || fail ".env has no ALLOWED_EMAIL_DOMAINS"
[ -n "$ORIGIN" ] || fail ".env has no PUBLIC_BASE_URL"
[ -n "$ADMIN_ID" ] || fail ".env names no admin in ADMIN_EMAILS"
TRADER_ID="smoke.trader@$DOMAIN"
MAKER_ID="smoke.maker@$DOMAIN"
if [ "$BIND" = "0.0.0.0" ]; then
    BASE="http://127.0.0.1:$PORT"
else
    BASE="http://$BIND:$PORT"
fi
echo "Walking $BASE as $TRADER_ID, $MAKER_ID and the first admin"

# --- 1. Profile set-up ---------------------------------------------------------------
STEP="1 (profile set-up)"

call nobody GET /api/config
expect 200
LEVEL=$(field '.degrees[0].level')
PROGRAMME=$(field '.degrees[0].programmes[0]')
SIGNUP_GRANT=$(field '.signup_grant_cr')
STAKE=$(field '.stake_presets_cr[0]')

call trader GET /api/me
if [ "$STATUS" = "200" ]; then
    fail "$TRADER_ID has a profile already: this database is not empty. Wipe it first."
fi
expect 403 PROFILE_REQUIRED
holds "the percent-encoded name did not arrive decoded" '.details.name == "Zoë Smoke"'
passed "a caller without a profile is asked to set one up"

call trader PUT /api/me/profile "$(profile_of smoke_trader)"
expect 200
holds "the profile was not created with its welcome grant" \
    '.username == "smoke_trader" and .signup_granted == true'
same_amount "$(field '.balance_cr')" "$SIGNUP_GRANT" \
    || fail "$CALL: the balance is $(field '.balance_cr'), the welcome grant is $SIGNUP_GRANT"
passed "the trader set up a profile and holds the welcome grant of $SIGNUP_GRANT"

call maker PUT /api/me/profile "$(profile_of Smoke_Maker)"
expect 422 VALIDATION_ERROR
call maker PUT /api/me/profile "$(profile_of smoke_trader)"
expect 409 USERNAME_TAKEN
passed "a username with a capital letter is refused, and so is one somebody holds"

call maker PUT /api/me/profile "$(profile_of smoke_maker)"
expect 200
call admin PUT /api/me/profile "$(profile_of smoke_admin)"
expect 200
call admin GET /api/me
expect 200
holds "the first address of ADMIN_EMAILS is not an admin" '.role == "ADMIN"'
passed "the maker and the admin set up profiles; the admin has the role ADMIN"

# --- 2. A market, proposed and approved ----------------------------------------------
STEP="2 (a market)"

PROPOSAL=$(jq -cn --arg close_at "$(days_from_now 2)" --arg resolve_by "$(days_from_now 3)" '{
    question: "Smoke test: does this deployment work?",
    yes_definition: "The smoke test passes.",
    no_definition: "The smoke test fails.",
    resolution_criteria: "The exit status of infra/deploy/smoke.sh.",
    resolution_source: "infra/deploy/smoke.sh",
    close_at: $close_at,
    resolve_by: $resolve_by,
    requested_tier: "DEFAULT"
}')
call maker POST /api/markets "$PROPOSAL"
expect 201
MARKET=$(field '.id')
passed "the maker proposed market $MARKET"

call trader POST "/api/admin/markets/$MARKET/approve" '{"tier":"DEFAULT"}'
expect 403 FORBIDDEN
call admin POST "/api/admin/markets/$MARKET/approve" '{"tier":"DEFAULT"}'
expect 200
holds "the approved market is not open for trading" '.status == "OPEN" and .trading_open == true'
passed "only the admin could approve it; it is open"

# --- 3. A trade ---------------------------------------------------------------------
STEP="3 (a trade)"

call trader GET "/api/markets/$MARKET/quote?side=BUY&outcome=YES&amount_cr=$STAKE"
expect 200
field '.shares' > /dev/null

IDEMPOTENCY_KEY=$(new_key)
TRADE_REQUEST=$(jq -cn --arg amount "$STAKE" '{side: "BUY", outcome: "YES", amount_cr: $amount}')
call trader POST "/api/markets/$MARKET/trades" "$TRADE_REQUEST"
expect 201
TRADE=$(field '.trade_id')
SHARES=$(field '.shares')
BALANCE_AFTER_TRADE=$(field '.balance_cr')
passed "the trader bought $SHARES YES shares for a stake of $STAKE"

call trader POST "/api/markets/$MARKET/trades" "$TRADE_REQUEST"
case $STATUS in
    200 | 201) ;;
    *) expect 201 ;;
esac
[ "$(field '.trade_id')" = "$TRADE" ] || fail "$CALL: the same Idempotency-Key made a second trade"
IDEMPOTENCY_KEY=
call trader GET /api/me
expect 200
same_amount "$(field '.balance_cr')" "$BALANCE_AFTER_TRADE" \
    || fail "$CALL: the balance is $(field '.balance_cr'), the trade left $BALANCE_AFTER_TRADE"
passed "the same request again is the same trade; the balance is $BALANCE_AFTER_TRADE"

# --- 4. The event stream --------------------------------------------------------------
STEP="4 (the event stream)"

CALL="GET /api/markets/$MARKET/stream as trader"
stream_result=0
curl --silent --no-buffer --max-time "$STREAM_SECONDS" --dump-header "$WORK/stream.headers" \
    --output "$WORK/stream" --header 'Accept: text/event-stream' \
    --header "X-User-Id: $TRADER_ID" "$BASE/api/markets/$MARKET/stream" || stream_result=$?
if [ "$stream_result" != "28" ]; then
    fail "$CALL: the stream ended within $STREAM_SECONDS seconds (curl exit $stream_result):" \
        "$(head -n 1 "$WORK/stream.headers" 2> /dev/null || true) $(head -c 300 "$WORK/stream" 2> /dev/null || true)"
fi
grep -q -i '^content-type: text/event-stream' "$WORK/stream.headers" \
    || fail "$CALL: the answer is no event stream: $(head -n 1 "$WORK/stream.headers")"
grep -q -i '^x-accel-buffering: no' "$WORK/stream.headers" \
    || fail "$CALL: the proxy does not pass on X-Accel-Buffering: no, so the portal would buffer the stream"
[ -s "$WORK/stream" ] || fail "$CALL: the stream sent nothing in $STREAM_SECONDS seconds"
passed "the event stream opens, sends, and is still open after $STREAM_SECONDS seconds"

# --- 5. A resolution -----------------------------------------------------------------
STEP="5 (a resolution)"

RESOLUTION='{"outcome":"YES","evidence":"The smoke test reached its fifth step."}'
call trader POST "/api/admin/markets/$MARKET/resolve" "$RESOLUTION"
expect 403 FORBIDDEN
call admin POST "/api/admin/markets/$MARKET/close" '{}'
expect 200
holds "the market is not closed" '.status == "CLOSED"'
call admin POST "/api/admin/markets/$MARKET/resolve" "$RESOLUTION"
expect 200
holds "the market is not resolved YES" '.status == "RESOLVED" and .outcome == "YES"'
call admin POST "/api/admin/markets/$MARKET/resolve" '{"outcome":"NO","evidence":"A second resolution."}'
expect 409
passed "only the admin could resolve the market, and only once"

BALANCE_AFTER_PAYOUT=$(sum_of "$BALANCE_AFTER_TRADE" "$SHARES")
call trader GET /api/me
expect 200
same_amount "$(field '.balance_cr')" "$BALANCE_AFTER_PAYOUT" \
    || fail "$CALL: the balance is $(field '.balance_cr'); $BALANCE_AFTER_TRADE plus one credit for each of $SHARES winning shares is $BALANCE_AFTER_PAYOUT"
passed "each winning share paid one credit: the trader holds $BALANCE_AFTER_PAYOUT"

# --- 6. A voucher, issued and redeemed ------------------------------------------------
STEP="6 (a voucher)"

call trader GET /api/vouchers/products
expect 200
PRODUCT=$(field '[.items[] | select(.affordable)][0].id')
call trader POST /api/vouchers "$(jq -cn --arg product "$PRODUCT" '{product_id: $product}')"
expect 201
VOUCHER=$(field '.id')
FIRST_CODE=$(field '.code')
holds "the voucher is for another product" --arg product "$PRODUCT" '.product_id == $product'
passed "the trader got a voucher for $PRODUCT"

call trader POST "/api/vouchers/$VOUCHER/code"
expect 200
CODE=$(field '.code')
[ "$CODE" != "$FIRST_CODE" ] || fail "$CALL: the fresh code is the one the voucher had"
holds "the code belongs to another voucher" --arg voucher "$VOUCHER" '.voucher_id == $voucher'

call trader POST /api/barista/vouchers/redeem "$(scan_of "$CODE")"
expect 403 FORBIDDEN
call admin POST /api/barista/vouchers/redeem "$(scan_of "$FIRST_CODE")"
[ "$STATUS" != "200" ] || fail "$CALL: the code that was replaced still redeems the voucher"
call admin POST /api/barista/vouchers/redeem "$(scan_of "$CODE")"
expect 200
holds "another voucher was redeemed" --arg voucher "$VOUCHER" '.voucher_id == $voucher'
call admin POST /api/barista/vouchers/redeem "$(scan_of "$CODE")"
[ "$STATUS" != "200" ] || fail "$CALL: the voucher was redeemed twice"
call trader GET /api/me/vouchers
expect 200
holds "the trader's voucher is not shown as redeemed" --arg voucher "$VOUCHER" \
    '.items[] | select(.id == $voucher) | .status == "REDEEMED"'
passed "a fresh code replaced the first one; the admin redeemed the voucher with it, once"

# --- 7. The leaderboard ----------------------------------------------------------------
STEP="7 (the leaderboard)"

call trader GET /api/leaderboard
expect 200
holds "the viewer's own row is missing" \
    '.viewer.username == "smoke_trader" and .viewer.is_viewer == true'
holds "the trader's row does not show one trade and one market, won" \
    '.viewer.trades == 1 and .viewer.settled_markets == 1 and .viewer.win_rate == 1'
holds "the trader is not on the list" '[.items[].username] | index("smoke_trader") != null'
passed "the leaderboard lists the trader with one trade and one market, won"

# --- 8. The books ----------------------------------------------------------------------
STEP="8 (the books)"

CALL="SELECT sum(balance_micro) FROM accounts"
TOTAL=$(docker compose -f compose.yaml exec -T postgres sh -c \
    'exec psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --no-align --tuples-only --command "SELECT coalesce(sum(balance_micro), 0) FROM accounts"' \
    < /dev/null) || fail "$CALL: could not ask Postgres"
[ "$TOTAL" = "0" ] || fail "$CALL: the accounts sum to $TOTAL micro-credits, not to zero"
passed "every account of the ledger sums to zero"

echo "The walk passed. This database now holds test data: delete it (make deploy-wipe CONFIRM=wipe)."
