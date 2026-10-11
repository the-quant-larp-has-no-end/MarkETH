#!/bin/sh
# Check a running MarkETH stack from the machine it runs on. Changes no data.
#
#   sh infra/deploy/check.sh                            the checks below, against the stack
#                                                       the .env of this tree describes
#   sh infra/deploy/check.sh public-endpoint            print "<address> <port>": this
#                                                       machine's public address and the
#                                                       port the stack is published on
#   sh infra/deploy/check.sh outside <address> <port>   from another machine: that
#                                                       address must not answer there
#
# `make deploy-check` runs the first form on the VM, then the other two to probe the VM
# from the laptop. The first form is for a stack started from compose.yaml alone: with
# compose.dev.yaml merged in, the proxy names a caller itself and check 2 fails.
#
# What the first form proves, in this order. It stops at the first check that fails.
#
#   1. The API is healthy and the app answers.
#   2. A request without the portal's identity headers is refused (401).
#   3. A request that names a caller is taken at its word: who can reach the port is
#      who the stack believes (docs/SERVER_INFRASTRUCTURE.md §3.4). The made-up caller
#      has no profile, so the answer is 403 PROFILE_REQUIRED and nothing is written.
#   4. An address outside the allowed domains is refused.
#   5. Two X-User-Id lines are no identity.
#   6. A request another site makes to the API with a visitor's browser is refused by
#      the proxy; the visitor's own window opening an API address from there is not.
#   7. The web app's container gets no answer from the API: what it asks the proxy
#      under /api/ is refused, an event stream included and whatever address it names
#      as the visitor's, while the same request through the published port is answered.
#   8. A body above NGINX_CLIENT_MAX_BODY_SIZE is refused by the proxy; one below is not.
#   9. The proxy's rate limit refuses part of a burst, and counts per caller.
#  10. With APP_ENV=production: the development identity files are absent, the running
#      proxy has no trace of them, and nginx is the only container with a published
#      port, bound to the one address .env names.
#  11. The media box, which turns what a broadcaster sends into a live stream, is
#      healthy and runs as compose.yaml asks: no network, a read-only root, and no
#      variable of a store or of an admin in its environment. GET /api/health says
#      that the API reaches it.
#  12. A playlist of a live stream is served to nobody the API did not admit: 401 without
#      identity headers, 403 for a caller the API refuses, and 403 from the proxy itself
#      for a request another site makes and for the web app's container.
#  13. The one location with a body limit of its own takes a piece of a live stream that
#      every other address refuses as too large, and refuses one above
#      BROADCAST_CHUNK_MAX_BYTES.
#  14. The running nginx and api have the limits of open files .env names
#      (NGINX_NOFILE_LIMIT, API_NOFILE_LIMIT), and one process serves the API: what the
#      API keeps in its own memory, such as the orders it counts per user while Redis
#      is away (SPEC A.4), is kept once.
#
# Needs curl, jq and docker. Check 7 runs node inside the web container, check 14 python
# inside the api container.

set -eu

HEALTH_WAIT_SECONDS=120
# The media box ends with its last live stream and starts again empty: about a second.
BOX_WAIT_SECONDS=30
REQUEST_SECONDS=10
OUTSIDE_CONNECT_SECONDS=8

fail() {
    echo "FAILED  $*" >&2
    exit 1
}

passed() {
    echo "ok      $*"
}

# The value of a variable of .env, without surrounding double quotes.
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

# `64k` and `1m`, as nginx writes sizes, in bytes.
bytes_of() {
    case $1 in
        *[kK]) echo $((${1%[kK]} * 1024)) ;;
        *[mM]) echo $((${1%[mM]} * 1048576)) ;;
        *) echo "$1" ;;
    esac
}

# request <curl arguments>: STATUS holds the HTTP status, $WORK/body the body.
request() {
    STATUS=$(curl --silent --show-error --max-time "$REQUEST_SECONDS" --output "$WORK/body" \
        --write-out '%{http_code}' "$@") || fail "no answer to: curl $*"
}

# from_the_web_app <path> [<address>]: GET http://nginx<path>, sent from inside the web
# container to the proxy on the network the two share. With <address>, the request
# names it as the visitor's, the way the portal does. STATUS holds the HTTP status,
# $WORK/body the body.
from_the_web_app() {
    docker compose -f compose.yaml exec -T web node -e '
        const headers = { accept: "application/json" };
        if (process.argv[2]) headers["x-original-forwarded-for"] = process.argv[2];
        fetch("http://nginx" + process.argv[1], { headers, signal: AbortSignal.timeout(10000) }).then(
            async (answer) => {
                process.stdout.write(answer.status + "\n" + (await answer.text()));
            },
            (error) => {
                console.error(String(error.cause || error));
                process.exit(1);
            },
        );
    ' "$@" < /dev/null > "$WORK/inside" \
        || fail "could not ask the proxy for $1 from inside the web container"
    STATUS=$(head -n 1 "$WORK/inside")
    tail -n +2 "$WORK/inside" > "$WORK/body"
}

# open_files_of <file>: "<soft> <hard>", the limit of open files in what a process says
# about itself in /proc/<pid>/limits.
open_files_of() {
    sed -n 's/^Max open files  *\([^ ][^ ]*\)  *\([^ ][^ ]*\) .*/\1 \2/p' "$1"
}

# The `code` of the API's error body, or nothing.
error_code() {
    jq -r '.code? // empty' "$WORK/body" 2> /dev/null || true
}

body_excerpt() {
    head -c 300 "$WORK/body" 2> /dev/null || true
}

expect() {
    wanted_status=$1
    wanted_code=$2
    what=$3
    if [ "$STATUS" != "$wanted_status" ] || [ "$(error_code)" != "$wanted_code" ]; then
        fail "$what: expected HTTP $wanted_status ${wanted_code:-without an error code}," \
            "got $STATUS $(body_excerpt)"
    fi
    passed "$what"
}

cd "$(dirname "$0")/../.."
[ -f .env ] || fail "no .env in $(pwd)"

# --- The two forms of the outside probe ------------------------------------------

if [ "${1-}" = "public-endpoint" ]; then
    address=$(ip -4 route get 1.1.1.1 | sed -n 's/.* src \([0-9.]*\).*/\1/p')
    [ -n "$address" ] || fail "this machine has no route to the internet to take its public address from"
    echo "$address $(setting NGINX_HTTP_PORT)"
    exit 0
fi

if [ "${1-}" = "outside" ]; then
    [ "$#" -eq 3 ] || fail "usage: sh infra/deploy/check.sh outside <address> <port>"
    result=0
    curl --silent --output /dev/null --connect-timeout "$OUTSIDE_CONNECT_SECONDS" \
        --max-time "$((OUTSIDE_CONNECT_SECONDS + 2))" "http://$2:$3/api/health" || result=$?
    case $result in
        7 | 28)
            passed "http://$2:$3 does not answer from here (curl exit $result): the port is closed to the internet"
            ;;
        0)
            fail "http://$2:$3 answers from here. The identity headers are plain text, so anyone" \
                "who reaches this port can act as anyone. Close the port (docs/SERVER_INFRASTRUCTURE.md §6)" \
                "before the stack serves another request."
            ;;
        *)
            fail "could not tell whether http://$2:$3 is closed (curl exit $result)"
            ;;
    esac
    exit 0
fi

[ "$#" -eq 0 ] || fail "unknown argument: $1"

# --- The stack this tree describes ---------------------------------------------------

for tool in curl jq docker; do
    command -v "$tool" > /dev/null || fail "$tool is not installed"
done

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

APP_ENV=$(setting APP_ENV)
BIND=$(setting NGINX_HTTP_BIND)
PORT=$(setting NGINX_HTTP_PORT)
# The made-up callers below are ids at the first allowed domain.
DOMAIN=$(setting ALLOWED_EMAIL_DOMAINS | cut -d, -f1)
ORIGIN=$(setting PUBLIC_BASE_URL)
[ -n "$BIND" ] || fail ".env has no NGINX_HTTP_BIND"
[ -n "$PORT" ] || fail ".env has no NGINX_HTTP_PORT"
[ -n "$DOMAIN" ] || fail ".env has no ALLOWED_EMAIL_DOMAINS"
if [ "$BIND" = "0.0.0.0" ]; then
    BASE="http://127.0.0.1:$PORT"
else
    BASE="http://$BIND:$PORT"
fi
echo "Checking $BASE (APP_ENV=$APP_ENV)"

# 1. Health --------------------------------------------------------------------------

waited=0
until curl --silent --fail --max-time "$REQUEST_SECONDS" --output "$WORK/body" "$BASE/api/health"; do
    [ "$waited" -lt "$HEALTH_WAIT_SECONDS" ] \
        || fail "GET /api/health gave no 200 within $HEALTH_WAIT_SECONDS seconds"
    sleep 2
    waited=$((waited + 2))
done
[ "$(jq -r '.postgres?' "$WORK/body")" = "up" ] || fail "the API is up but Postgres is not: $(body_excerpt)"
passed "the API is healthy: $(jq -c . "$WORK/body")"

request "$BASE/"
[ "$STATUS" = "200" ] || fail "GET / answered $STATUS, not the app"
passed "the app answers"

# 2. No identity ---------------------------------------------------------------------

request "$BASE/api/me"
expect 401 UNAUTHENTICATED "a request without identity headers is refused"

# 3. A named caller is believed ------------------------------------------------------

request --header "X-User-Id: deploy.check@$DOMAIN" --header 'X-User-Name: Zo%C3%AB%20Check' "$BASE/api/me"
expect 403 PROFILE_REQUIRED "a request that names a caller is answered as that caller"
jq -e --arg email "deploy.check@$DOMAIN" --arg name 'Zoë Check' \
    '.details.email == $email and .details.name == $name' "$WORK/body" > /dev/null \
    || fail "the API did not see the caller the request named: $(body_excerpt)"
passed "the caller's id and percent-encoded name arrive intact"

# 4. Outside the allowed domains -------------------------------------------------------

request --header 'X-User-Id: someone@not-allowed.example' "$BASE/api/me"
expect 403 FORBIDDEN "an address outside the allowed domains is refused"

# 5. Two identities are none ---------------------------------------------------------

request --header "X-User-Id: deploy.check@$DOMAIN" --header "X-User-Id: deploy.other@$DOMAIN" "$BASE/api/me"
case "$STATUS $(error_code)" in
    "401 UNAUTHENTICATED" | "403 FORBIDDEN") passed "two X-User-Id lines are refused ($STATUS)" ;;
    *) fail "two X-User-Id lines: expected 401 UNAUTHENTICATED or 403 FORBIDDEN, got $STATUS $(body_excerpt)" ;;
esac

# 6. Requests another site makes -----------------------------------------------------
# /api/config is public and the API answers it with 200, so a 403 here is the proxy's.
# No route of the API answers a caller without an identity with 403 either, which makes
# the 403 on the stream path the proxy's too.

request --header 'Sec-Fetch-Site: cross-site' --header 'Sec-Fetch-Mode: cors' \
    --header 'Sec-Fetch-Dest: empty' "$BASE/api/config"
expect 403 FORBIDDEN "a request another site makes to the API is refused by the proxy"
request --header 'Sec-Fetch-Site: same-site' --header 'Sec-Fetch-Mode: cors' \
    --header 'Sec-Fetch-Dest: empty' --header 'Accept: text/event-stream' \
    "$BASE/api/markets/00000000-0000-0000-0000-000000000000/stream"
expect 403 FORBIDDEN "so is an event stream another team's app opens"
request --header 'Sec-Fetch-Site: cross-site' --header 'Sec-Fetch-Mode: navigate' \
    --header 'Sec-Fetch-Dest: iframe' "$BASE/api/config"
expect 403 FORBIDDEN "and an API address another site loads into a frame"
request --header 'Sec-Fetch-Site: cross-site' --header 'Sec-Fetch-Mode: navigate' \
    --header 'Sec-Fetch-Dest: document' "$BASE/api/config"
[ "$STATUS" = "200" ] || fail "a window that opens an API address from another site: expected 200, got $STATUS $(body_excerpt)"
passed "a visitor's own window that opens an API address from another site is answered"

# 7. The web app's container -----------------------------------------------------------
# The proxy hands a request under /api/ on with the headers it came with, and the web
# app's container reaches the proxy on the network the two share, past the portal. The
# proxy therefore refuses that peer under /api/. /api/config is public and the API
# answers it with 200, so a 403 here is the proxy's; so is the 403 on the stream path
# (see check 6). The second request names a public address as the visitor's: the proxy
# has to judge the peer of the connection, not what a request says about it.

request "$BASE/api/config"
[ "$STATUS" = "200" ] || fail "GET /api/config through the published port: expected 200, got $STATUS $(body_excerpt)"
from_the_web_app /api/config
expect 403 FORBIDDEN "the web app's container is refused by the proxy under /api/"
from_the_web_app /api/config 203.0.113.7
expect 403 FORBIDDEN "whatever address it names as the visitor's"
from_the_web_app /api/markets/00000000-0000-0000-0000-000000000000/stream
expect 403 FORBIDDEN "and it opens no event stream"
passed "the same /api/config through the published port is answered"

# 8. The body limit ------------------------------------------------------------------

LIMIT=$(bytes_of "$(setting NGINX_CLIENT_MAX_BODY_SIZE)")
head -c "$((LIMIT + 1024))" /dev/zero > "$WORK/above"
head -c "$((LIMIT - 1024))" /dev/zero > "$WORK/below"
request --request POST --header "Origin: $ORIGIN" --header 'Content-Type: application/json' \
    --data-binary "@$WORK/above" "$BASE/api/markets"
[ "$STATUS" = "413" ] || fail "a body of $((LIMIT + 1024)) bytes: expected 413, got $STATUS $(body_excerpt)"
passed "a body above $(setting NGINX_CLIENT_MAX_BODY_SIZE) is refused (413)"
request --request POST --header "Origin: $ORIGIN" --header 'Content-Type: application/json' \
    --data-binary "@$WORK/below" "$BASE/api/markets"
[ "$STATUS" != "413" ] || fail "a body of $((LIMIT - 1024)) bytes was refused as too large"
passed "a body below it reaches the API ($STATUS)"

# 9. The rate limit ------------------------------------------------------------------
# One curl process sends the burst on parallel connections: started one process at a
# time, the requests would arrive slower than the bucket refills.

BURST=$(setting NGINX_RATE_LIMIT_BURST)
REQUESTS=$((BURST * 4))
[ "$REQUESTS" -le 300 ] || REQUESTS=300
count=0
: > "$WORK/burst"
while [ "$count" -lt "$REQUESTS" ]; do
    printf 'url = "%s/api/config"\noutput = "/dev/null"\n' "$BASE" >> "$WORK/burst"
    count=$((count + 1))
done
curl --silent --parallel --parallel-immediate --parallel-max "$REQUESTS" --max-time 30 \
    --header "X-User-Id: deploy.burst@$DOMAIN" --write-out '%{http_code}\n' \
    --config "$WORK/burst" > "$WORK/burst.codes" || true
answered=$(grep -c '^200$' "$WORK/burst.codes" || true)
refused=$(grep -c '^429$' "$WORK/burst.codes" || true)
if [ "$answered" -eq 0 ] || [ "$refused" -eq 0 ]; then
    fail "a burst of $REQUESTS requests by one caller: $answered answered, $refused refused with 429;" \
        "expected some of each (NGINX_RATE_LIMIT=$(setting NGINX_RATE_LIMIT), burst $BURST)." \
        "Statuses seen: $(sort "$WORK/burst.codes" | uniq -c | tr '\n' ' ')"
fi
passed "of a burst of $REQUESTS requests by one caller, $answered are answered and $refused refused (429)"
request --header "X-User-Id: deploy.other@$DOMAIN" "$BASE/api/config"
[ "$STATUS" = "200" ] || fail "another caller from the same address got $STATUS right after the burst: the limit is not per caller"
passed "another caller from the same address is not limited"

# 10. Production only ----------------------------------------------------------------

if [ "$APP_ENV" = "production" ]; then
    [ ! -e compose.dev.yaml ] || fail "compose.dev.yaml exists in a production tree"
    # The one name Compose merges over compose.yaml without being told to.
    [ ! -e compose.override.yaml ] || fail "compose.override.yaml exists in a production tree"
    [ ! -e infra/nginx/dev ] || fail "infra/nginx/dev exists in a production tree"
    passed "the development overlay and its identity files are absent"

    docker compose -f compose.yaml exec -T nginx nginx -T < /dev/null > "$WORK/nginx.conf" 2> /dev/null \
        || fail "could not read the configuration of the running nginx"
    if grep -n -i -E 'dev_user|__dev' "$WORK/nginx.conf" >&2; then
        fail "the running nginx configuration names a development identity (lines above)"
    fi
    passed "the running proxy has no development identity"

    proxy=$(docker compose -f compose.yaml ps --quiet nginx < /dev/null) || fail "could not ask Compose for the nginx container"
    [ -n "$proxy" ] || fail "no nginx container is running"
    # The project the running stack belongs to, as Compose labelled its containers.
    PROJECT=$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.project" }}' "$proxy" < /dev/null) \
        || fail "could not read the Compose project of the nginx container"
    [ -n "$PROJECT" ] || fail "the nginx container carries no Compose project"
    # Every address the port is bound to. The probe from outside asks the public IPv4
    # address, so a second binding, on IPv6, would be a way in that nothing looks at.
    published=$(docker port "$proxy" 80/tcp < /dev/null | tr '\n' ' ') || true
    published=${published% }
    [ "$published" = "$BIND:$PORT" ] || fail "nginx is published on '$published', not on $BIND:$PORT alone"
    others=$(docker ps --filter "label=com.docker.compose.project=$PROJECT" \
        --format '{{.Label "com.docker.compose.service"}} {{.Ports}}' | grep -e '->' | grep -v '^nginx ' || true)
    [ -z "$others" ] || fail "containers other than nginx publish a port: $others"
    passed "nginx on $published is the only published port of the stack"
else
    echo "skipped the checks of a production tree (APP_ENV=$APP_ENV)"
fi

# 11. The media box --------------------------------------------------------------------
# ffmpeg reads bytes a person chose, so the box is the one container that must reach
# nothing. What Docker says about the running container is held against what
# compose.yaml asks for. A variable of a store or of an admin would be in its
# environment only if somebody handed it the env file; names are printed, never values.

box=$(docker compose -f compose.yaml ps --quiet media < /dev/null) || fail "could not ask Compose for the media container"
[ -n "$box" ] || fail "no media container is running"
waited=0
until [ "$(docker inspect --format '{{ .State.Health.Status }}' "$box" < /dev/null)" = "healthy" ]; do
    [ "$waited" -lt "$BOX_WAIT_SECONDS" ] \
        || fail "the media box is not healthy after $BOX_WAIT_SECONDS seconds: docker compose -f compose.yaml logs media"
    sleep 2
    waited=$((waited + 2))
done
passed "the media box is healthy"

isolation=$(docker inspect --format '{{ .HostConfig.NetworkMode }} {{ .HostConfig.ReadonlyRootfs }}' "$box" < /dev/null) \
    || fail "could not read how the media container runs"
[ "$isolation" = "none true" ] \
    || fail "the media box runs with network mode and read-only root '$isolation', not 'none true'"
docker inspect --format '{{ range .Config.Env }}{{ println . }}{{ end }}' "$box" < /dev/null > "$WORK/box.env" \
    || fail "could not read the environment of the media container"
handed=$(grep -E '^((POSTGRES|REDIS|SCYLLA)[A-Z0-9_]*|ADMIN_EMAILS)=' "$WORK/box.env" | cut -d= -f1 | tr '\n' ' ')
[ -z "$handed" ] || fail "the media box is handed variables that are none of its own: $handed"
passed "it has no network, a read-only root, and no variable of a store or of an admin"

request "$BASE/api/health"
[ "$STATUS" = "200" ] || fail "GET /api/health answered $STATUS: $(body_excerpt)"
[ "$(jq -r '.media?' "$WORK/body")" = "up" ] || fail "the API does not reach the media box: $(body_excerpt)"
passed "the API reaches it: $(jq -c . "$WORK/body")"

# 12. Video is served to nobody the API did not admit -------------------------------------
# The proxy asks the API about the caller of every playlist and every segment before it
# serves one. The broadcast is made up: nothing is there to serve, and each answer is
# about the caller. A request without identity headers gets the API's 401, so a 403 for
# such a request is the proxy's own.

PLAYLIST=/api/broadcasts/00000000-0000-0000-0000-000000000000/hls/index.m3u8
request "$BASE$PLAYLIST"
expect 401 UNAUTHENTICATED "a playlist is refused to a request without identity headers"
request --header "X-User-Id: deploy.check@$DOMAIN" "$BASE$PLAYLIST"
expect 403 FORBIDDEN "and to a caller the API does not let watch"
request --header 'Sec-Fetch-Site: cross-site' --header 'Sec-Fetch-Mode: no-cors' \
    --header 'Sec-Fetch-Dest: video' "$BASE$PLAYLIST"
expect 403 FORBIDDEN "a playlist another site puts into its page is refused by the proxy"
from_the_web_app "$PLAYLIST"
expect 403 FORBIDDEN "and so is the web app's container"

# 13. The body limit of a piece of a live stream -------------------------------------------
# The body of check 8, just above the limit of the server, is too large everywhere but at
# the address of a piece. The caller is made up and has no profile: the API refuses the
# piece, which is an answer of the API and so no 413 of the proxy.

PIECE_LIMIT=$(setting BROADCAST_CHUNK_MAX_BYTES)
[ -n "$PIECE_LIMIT" ] || fail ".env has no BROADCAST_CHUNK_MAX_BYTES"
[ "$PIECE_LIMIT" -gt "$((LIMIT + 1024))" ] \
    || fail "BROADCAST_CHUNK_MAX_BYTES=$PIECE_LIMIT is not above NGINX_CLIENT_MAX_BODY_SIZE: the check has no body between the two"
PIECES="$BASE/api/broadcasts/00000000-0000-0000-0000-000000000000/chunks?part=deploy-check&seq=0"
head -c "$((PIECE_LIMIT + 1024))" /dev/zero > "$WORK/above-a-piece"
request --request PUT --header "Origin: $ORIGIN" --header 'Content-Type: application/json' \
    --data-binary "@$WORK/above" "$BASE/api/me/profile"
[ "$STATUS" = "413" ] || fail "a body of $((LIMIT + 1024)) bytes at /api/me/profile: expected 413, got $STATUS $(body_excerpt)"
request --request POST --header "Origin: $ORIGIN" --header 'Content-Type: application/octet-stream' \
    --header "X-User-Id: deploy.check@$DOMAIN" --data-binary "@$WORK/above" "$PIECES"
[ "$STATUS" != "413" ] || fail "a piece of $((LIMIT + 1024)) bytes was refused as too large"
passed "a body the proxy refuses elsewhere (413) reaches the API as a piece of a live stream ($STATUS)"
request --request POST --header "Origin: $ORIGIN" --header 'Content-Type: application/octet-stream' \
    --header "X-User-Id: deploy.check@$DOMAIN" --data-binary "@$WORK/above-a-piece" "$PIECES"
expect 413 VALIDATION_ERROR "a piece above BROADCAST_CHUNK_MAX_BYTES=$PIECE_LIMIT is refused by the proxy"

# 14. Open files, and the one API process ---------------------------------------------
# A limit is read from the process it holds for, in /proc. For nginx that is process 1,
# the master, whose limit every worker has. For the API it is whatever holds a
# listening socket in the api container: the python below prints one line per such
# process, "<pid> <soft> <hard>". One line is one process that serves the API. A command
# with `--workers`, or a reloader, shows as several: the socket is theirs together.

NGINX_FILES=$(setting NGINX_NOFILE_LIMIT)
API_FILES=$(setting API_NOFILE_LIMIT)
[ -n "$NGINX_FILES" ] || fail ".env has no NGINX_NOFILE_LIMIT"
[ -n "$API_FILES" ] || fail ".env has no API_NOFILE_LIMIT"

docker compose -f compose.yaml exec -T nginx cat /proc/1/limits < /dev/null > "$WORK/limits" \
    || fail "could not read the limits of the running nginx"
found=$(open_files_of "$WORK/limits")
[ "$found" = "$NGINX_FILES $NGINX_FILES" ] \
    || fail "nginx runs with a limit of open files of '$found' (soft, hard), not NGINX_NOFILE_LIMIT=$NGINX_FILES"
passed "nginx may hold $NGINX_FILES files open (NGINX_NOFILE_LIMIT)"

docker compose -f compose.yaml exec -T api python -c '
import os

listening = set()
for table in ("/proc/net/tcp", "/proc/net/tcp6"):
    try:
        with open(table) as rows:
            sockets = [row.split() for row in rows][1:]
    except OSError:
        continue
    listening |= {"socket:[%s]" % socket[9] for socket in sockets if socket[3] == "0A"}

for pid in sorted((name for name in os.listdir("/proc") if name.isdigit()), key=int):
    try:
        held = {os.readlink("/proc/%s/fd/%s" % (pid, fd)) for fd in os.listdir("/proc/%s/fd" % pid)}
        if not held & listening:
            continue
        with open("/proc/%s/limits" % pid) as limits:
            files = [line.split()[3:5] for line in limits if line.startswith("Max open files")]
        print(pid, *files[0])
    except OSError:
        continue
' < /dev/null > "$WORK/serving" || fail "could not ask the api container which of its processes listen"
serving=$(grep -c . "$WORK/serving" || true)
[ "$serving" = "1" ] \
    || fail "$serving processes of the api container hold its listening socket, not one: $(tr '\n' ';' < "$WORK/serving")"
read -r pid soft hard < "$WORK/serving"
[ "$soft $hard" = "$API_FILES $API_FILES" ] \
    || fail "the API (process $pid) runs with a limit of open files of '$soft $hard' (soft, hard), not API_NOFILE_LIMIT=$API_FILES"
passed "one process serves the API, and it may hold $API_FILES files open (API_NOFILE_LIMIT)"

echo "All checks passed."
