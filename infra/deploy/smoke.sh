#!/bin/sh
# Walk a running MarkETH stack from profile set-up to a redeemed voucher, through the
# proxy, as four callers named by forged identity headers.
#
#   sh infra/deploy/smoke.sh        on the machine the stack runs on, from its tree
#
# It is for a stack started from compose.yaml alone. With compose.dev.yaml merged
# in, the proxy names the caller itself, whatever a request says, and the walk stops at
# its first step.
#
# IT WRITES DATA. It registers three made-up people and the first address of
# ADMIN_EMAILS, makes one of the three a barista, and leaves a resolved market, a trade,
# a redeemed voucher and a redeemed code behind. Run it on an empty database and delete
# that database afterwards: on the VM `make deploy-smoke CONFIRM=smoke`, then
# `make deploy-wipe CONFIRM=wipe`. It refuses to start when its trader has a profile
# already.
#
# Each step calls the operations named beside it. Steps run in this order and the walk
# stops at the first that fails, saying which call it was.
#
#   1. Profile set-up     GET /api/config, GET /api/me, PUT /api/me/profile
#   2. A market           POST /api/markets, POST /api/admin/markets/{id}/approve
#   3. A trade            GET /api/markets/{id}/quote, POST /api/markets/{id}/trades
#   4. The event stream   GET /api/markets/{id}/stream
#   5. A resolution       POST /api/admin/markets/{id}/close and .../resolve
#   6. A barista          GET /api/admin/users, POST /api/admin/users/{id}/role,
#                         POST /api/barista/vouchers/redeem before the role is given
#   7. A voucher          GET /api/vouchers/products, POST /api/vouchers,
#                         POST /api/vouchers/{id}/code,
#                         POST /api/barista/vouchers/redeem, GET /api/me/vouchers
#   8. A code             POST /api/admin/code-batches, POST /api/codes/redeem
#   9. The leaderboard    GET /api/leaderboard
#  10. The books          every account of the ledger sums to zero (SPEC §1)
#
# Who is who. The admin is the first address of ADMIN_EMAILS, a bootstrap admin: an admin
# by the stack's configuration, from the first request on. Nobody else has a role until
# an admin gives them one, so the barista starts as a plain user like the trader and the
# maker, and is appointed in step 6. The maker is an admin for the length of step 8 and
# a plain user again when it ends.
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
# <who> is trader, maker, barista, admin or nobody. STATUS holds the HTTP status,
# $WORK/body the body. IDEMPOTENCY_KEY, when set, is sent along.
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
        barista)
            id=$BARISTA_ID
            name='Bea%20Smoke'
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

# profile_of <username> [<policy accepted: true or false>]: the body of a profile save.
# The save that creates a profile has to accept the usage policy.
profile_of() {
    jq -cn --arg username "$1" --arg level "$LEVEL" --arg programme "$PROGRAMME" \
        --argjson accepted "${2-true}" \
        '{username: $username, degree_level: $level, degree_programme: $programme,
          policy_accepted: $accepted}'
}

# proposal_of <policy accepted: true or false>: the body of a proposal. Every proposal
# accepts the usage policy again. It asks for no liquidity: the admin picks the tier
# when approving.
proposal_of() {
    jq -cn --arg close_at "$(days_from_now 2)" --arg resolve_by "$(days_from_now 3)" \
        --argjson accepted "$1" '{
            question: "Smoke test: does this deployment work?",
            yes_definition: "The smoke test passes.",
            no_definition: "The smoke test fails.",
            resolution_criteria: "The exit status of infra/deploy/smoke.sh.",
            resolution_source: "infra/deploy/smoke.sh",
            close_at: $close_at,
            resolve_by: $resolve_by,
            policy_accepted: $accepted
        }'
}

role_of() {
    jq -cn --arg role "$1" '{role: $role}'
}

# The body that carries a code, a barista's scan and the redemption of a code alike:
# the code travels in no address.
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
# The three made-up people are ids at the first allowed domain.
DOMAIN=$(setting ALLOWED_EMAIL_DOMAINS | cut -d, -f1)
ORIGIN=$(setting PUBLIC_BASE_URL)
ADMIN_ID=$(setting ADMIN_EMAILS | cut -d, -f1)
[ -n "$PORT" ] || fail ".env has no NGINX_HTTP_PORT"
[ -n "$DOMAIN" ] || fail ".env has no ALLOWED_EMAIL_DOMAINS"
[ -n "$ORIGIN" ] || fail ".env has no PUBLIC_BASE_URL"
[ -n "$ADMIN_ID" ] || fail ".env names no admin in ADMIN_EMAILS"
TRADER_ID="smoke.trader@$DOMAIN"
MAKER_ID="smoke.maker@$DOMAIN"
BARISTA_ID="smoke.barista@$DOMAIN"
if [ "$BIND" = "0.0.0.0" ]; then
    BASE="http://127.0.0.1:$PORT"
else
    BASE="http://$BIND:$PORT"
fi
echo "Walking $BASE as $TRADER_ID, $MAKER_ID, $BARISTA_ID and the first admin"

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

call trader PUT /api/me/profile "$(profile_of smoke_trader false)"
expect 422 VALIDATION_ERROR
holds "the refusal does not name the field policy_accepted" '.details.field == "policy_accepted"'
call trader GET /api/me
expect 403 PROFILE_REQUIRED
passed "nobody gets a profile without accepting the usage policy"

call trader PUT /api/me/profile "$(profile_of smoke_trader)"
expect 200
holds "the profile was not created with its welcome grant" \
    '.username == "smoke_trader" and .signup_granted == true'
TRADER_USER=$(field '.id')
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
MAKER_USER=$(field '.id')
call barista PUT /api/me/profile "$(profile_of smoke_barista)"
expect 200
holds "a person who is in no list did not start as a plain user" '.role == "USER"'
call admin PUT /api/me/profile "$(profile_of smoke_admin)"
expect 200
ADMIN_USER=$(field '.id')
call admin GET /api/me
expect 200
holds "the first address of ADMIN_EMAILS is not an admin" '.role == "ADMIN"'
passed "the maker, the barista-to-be and the admin set up profiles; only the admin has a role"

# --- 2. A market, proposed and approved ----------------------------------------------
STEP="2 (a market)"

call maker POST /api/markets "$(proposal_of false)"
expect 422 VALIDATION_ERROR
call maker POST /api/markets "$(proposal_of true)"
expect 201
MARKET=$(field '.id')
passed "the maker proposed market $MARKET; a proposal that does not accept the usage policy is refused"

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

# --- 6. A barista, appointed by the admin ---------------------------------------------
STEP="6 (a barista)"

call admin GET '/api/admin/users?q=smoke_barista'
expect 200
holds "the search does not find exactly the barista-to-be, a plain user" \
    '(.items | length) == 1 and .items[0].username == "smoke_barista"
     and .items[0].role == "USER" and .items[0].bootstrap_admin == false'
BARISTA_USER=$(field '.items[0].id')
call admin GET '/api/admin/users?role=ADMIN'
expect 200
holds "the admin is not listed as a bootstrap admin" --arg admin "$ADMIN_USER" \
    '[.items[] | select(.id == $admin and .role == "ADMIN" and .bootstrap_admin)] | length == 1'
passed "the admin panel lists the barista-to-be as a plain user and the admin as a bootstrap admin"

# A plain user gives no role and scans no voucher. The code is made up: the refusal
# comes before anybody looks at it.
call trader POST "/api/admin/users/$BARISTA_USER/role" "$(role_of BARISTA)"
expect 403 FORBIDDEN
call barista POST "/api/admin/users/$TRADER_USER/role" "$(role_of BARISTA)"
expect 403 FORBIDDEN
call barista POST /api/barista/vouchers/redeem "$(scan_of 00000000)"
expect 403 FORBIDDEN
call admin POST "/api/admin/users/$ADMIN_USER/role" "$(role_of USER)"
expect 403 FORBIDDEN
call admin GET /api/me
expect 200
holds "the admin's own role changed" '.role == "ADMIN"'
passed "a plain user gives no role, and the admin cannot change their own"

call admin POST "/api/admin/users/$BARISTA_USER/role" "$(role_of BARISTA)"
expect 200
holds "the account did not get the role BARISTA" --arg user "$BARISTA_USER" \
    '.id == $user and .role == "BARISTA" and .bootstrap_admin == false'
call barista GET /api/me
expect 200
holds "the barista's next request does not carry the role" '.role == "BARISTA"'
passed "the admin made $BARISTA_ID a barista"

# --- 7. A voucher, issued and redeemed ------------------------------------------------
STEP="7 (a voucher)"

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
call barista POST /api/barista/vouchers/redeem "$(scan_of "$FIRST_CODE")"
[ "$STATUS" != "200" ] || fail "$CALL: the code that was replaced still redeems the voucher"
call barista POST /api/barista/vouchers/redeem "$(scan_of "$CODE")"
expect 200
holds "another voucher was redeemed, or the scan that redeems calls itself a repeat" \
    --arg voucher "$VOUCHER" '.voucher_id == $voucher and .repeat == false'
REDEEMED_AT=$(field '.redeemed_at')

# The answer to a scan can get lost on the way to the barista's phone. The same barista
# scanning again right away is shown the redemption that happened, marked as a repeat.
# To anybody else who may scan, the voucher is used up.
call barista POST /api/barista/vouchers/redeem "$(scan_of "$CODE")"
expect 200
holds "the barista's second scan is not the same redemption, marked as a repeat" \
    --arg voucher "$VOUCHER" --arg at "$REDEEMED_AT" \
    '.voucher_id == $voucher and .repeat == true and .redeemed_at == $at'
call admin POST /api/barista/vouchers/redeem "$(scan_of "$CODE")"
expect 400 CODE_INVALID
call trader GET /api/me/vouchers
expect 200
holds "the trader's voucher is not shown as redeemed" --arg voucher "$VOUCHER" \
    '.items[] | select(.id == $voucher) | .status == "REDEEMED"'
passed "a fresh code replaced the first one; the barista redeemed the voucher with it, once"
passed "the barista's second scan repeats that answer; somebody else's scan finds the voucher used up"

# --- 8. A code, handed out by an admin and redeemed by a user ---------------------------
STEP="8 (a code)"

call admin POST /api/admin/code-batches "$(jq -cn --arg expires_at "$(days_from_now 2)" \
    '{label: "Smoke test", count: 1, expires_at: $expires_at, one_per_user: true}')"
expect 201
GIFT=$(field '.codes[0].code')
GIFT_VALUE=$(field '.batch.value_cr')
passed "the admin made a batch of one code worth $GIFT_VALUE"

# No admin redeems a code: not the one who made the batch, and not another one either.
# The maker is that other admin for three calls.
call admin POST "/api/admin/users/$MAKER_USER/role" "$(role_of ADMIN)"
expect 200
call maker POST /api/codes/redeem "$(scan_of "$GIFT")"
expect 403 FORBIDDEN
call admin POST /api/codes/redeem "$(scan_of "$GIFT")"
expect 403 FORBIDDEN
call admin POST "/api/admin/users/$MAKER_USER/role" "$(role_of USER)"
expect 200
call maker GET /api/me
expect 200
holds "the maker is not a plain user again" '.role == "USER"'
passed "neither the admin who made the batch nor another admin could redeem the code"

call trader GET /api/me
expect 200
BALANCE_BEFORE_CODE=$(field '.balance_cr')
call trader POST /api/codes/redeem "$(scan_of "$GIFT")"
expect 200
same_amount "$(field '.value_cr')" "$GIFT_VALUE" \
    || fail "$CALL: the code was worth $(field '.value_cr'), the batch says $GIFT_VALUE"
BALANCE_AFTER_CODE=$(sum_of "$BALANCE_BEFORE_CODE" "$GIFT_VALUE")
same_amount "$(field '.balance_cr')" "$BALANCE_AFTER_CODE" \
    || fail "$CALL: the balance is $(field '.balance_cr'); $BALANCE_BEFORE_CODE plus the code is $BALANCE_AFTER_CODE"
call barista POST /api/codes/redeem "$(scan_of "$GIFT")"
expect 400 CODE_INVALID
passed "the trader redeemed it and holds $BALANCE_AFTER_CODE; the code works once"

# --- 9. The leaderboard ----------------------------------------------------------------
STEP="9 (the leaderboard)"

call trader GET /api/leaderboard
expect 200
holds "the viewer's own row is missing" \
    '.viewer.username == "smoke_trader" and .viewer.is_viewer == true'
holds "the trader's row does not show one trade and one market, won" \
    '.viewer.trades == 1 and .viewer.settled_markets == 1 and .viewer.win_rate == 1'
holds "the trader is not on the list" '[.items[].username] | index("smoke_trader") != null'
passed "the leaderboard lists the trader with one trade and one market, won"

# --- 10. The books ---------------------------------------------------------------------
STEP="10 (the books)"

CALL="SELECT sum(balance_micro) FROM accounts"
TOTAL=$(docker compose -f compose.yaml exec -T postgres sh -c \
    'exec psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --no-align --tuples-only --command "SELECT coalesce(sum(balance_micro), 0) FROM accounts"' \
    < /dev/null) || fail "$CALL: could not ask Postgres"
[ "$TOTAL" = "0" ] || fail "$CALL: the accounts sum to $TOTAL micro-credits, not to zero"
passed "every account of the ledger sums to zero"

echo "The walk passed. This database now holds test data: delete it (make deploy-wipe CONFIRM=wipe)."
