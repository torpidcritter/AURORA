#!/usr/bin/env bash
# Post-deploy smoke test for an Aurora stack. Read-mostly: it signs in, reads data and runs one forecast.
# With SMOKE_TRAIN=1 it also starts a 1-epoch training job (creates a model version), so use that only on
# staging or CI, never on a production tenant.
#
#   SMOKE_EMAIL=admin@example.com SMOKE_PASSWORD=... scripts/smoke-test.sh
#
# Env: WEB_URL (default http://localhost:3100), API_URL (default http://127.0.0.1:8100),
#      SMOKE_EMAIL, SMOKE_PASSWORD (required), SMOKE_TRAIN=1 (optional), METRICS_TOKEN (optional).
set -euo pipefail

WEB_URL="${WEB_URL:-http://localhost:3100}"
API_URL="${API_URL:-http://127.0.0.1:8100}"
: "${SMOKE_EMAIL:?set SMOKE_EMAIL}"
: "${SMOKE_PASSWORD:?set SMOKE_PASSWORD}"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
JAR="$WORK/cookies"
ORIGIN="$WEB_URL"
pass=0

ok() { pass=$((pass + 1)); printf '  ok   %s\n' "$1"; }
fail() { printf '  FAIL %s\n' "$1" >&2; [ -f "$WORK/body" ] && head -c 600 "$WORK/body" >&2 && echo >&2; exit 1; }
json() { python3 -c "import json,sys; d=json.load(open('$WORK/body')); print($1)"; }

# status METHOD URL [curl args...] -> prints HTTP status, body in $WORK/body, headers in $WORK/headers
status() {
  local method="$1" url="$2"; shift 2
  curl -sS -o "$WORK/body" -D "$WORK/headers" -w '%{http_code}' -X "$method" -b "$JAR" -c "$JAR" "$@" "$url"
}
mutate() { status "$1" "$2" -H "Origin: $ORIGIN" -H "x-aurora-csrf: 1" -H "content-type: application/json" "${@:3}"; }

echo "Aurora smoke test: web=$WEB_URL api=$API_URL"

[ "$(status GET "$API_URL/health/live")" = 200 ] || fail "API liveness"
ok "API liveness"
[ "$(status GET "$API_URL/health/ready")" = 200 ] || fail "API readiness (database, Redis, storage)"
ok "API readiness: $(json "', '.join(f'{k}={\"ok\" if v else \"down\"}' for k, v in d['components'].items())")"

code=$(status GET "$API_URL/metrics")
if [ -n "${METRICS_TOKEN:-}" ]; then
  [ "$code" = 401 ] || fail "/metrics should require the bearer token (got $code)"
  [ "$(status GET "$API_URL/metrics" -H "Authorization: Bearer $METRICS_TOKEN")" = 200 ] || fail "/metrics with token"
  grep -q '^aurora_http_requests_total' "$WORK/body" || fail "/metrics has no aurora_http_requests_total"
  ok "/metrics protected by token and exporting aurora_* series"
else
  [ "$code" = 200 ] || fail "/metrics (got $code)"
  ok "/metrics reachable on the API port (set METRICS_TOKEN in production)"
fi

[ "$(status GET "$WEB_URL/login")" = 200 ] || fail "web /login"
grep -qi '^content-security-policy:' "$WORK/headers" || fail "web sends no Content-Security-Policy"
grep -qi '^x-frame-options: *deny' "$WORK/headers" || fail "web sends no X-Frame-Options: DENY"
ok "web serves /login with CSP and frame protection"

code=$(status POST "$WEB_URL/api/auth/login" -H "content-type: application/json" \
  --data "{\"email\":\"$SMOKE_EMAIL\",\"password\":\"x\"}")
[ "$code" = 403 ] || fail "login without Origin/CSRF header should be refused (got $code)"
ok "BFF refuses a login without the CSRF header"

code=$(mutate POST "$WEB_URL/api/auth/login" \
  --data "$(python3 -c 'import json,os; print(json.dumps({"email": os.environ["SMOKE_EMAIL"], "password": os.environ["SMOKE_PASSWORD"]}))')")
[ "$code" = 200 ] || fail "sign in through the BFF (got $code)"
grep -i '^set-cookie: *aurora_at=' "$WORK/headers" | grep -qi 'httponly' || fail "access cookie is not HttpOnly"
grep -i '^set-cookie: *aurora_at=' "$WORK/headers" | grep -qi 'samesite=strict' || fail "access cookie is not SameSite=Strict"
ok "signed in; session cookies are HttpOnly and SameSite=Strict"

[ "$(status GET "$WEB_URL/api/proxy/auth/me")" = 200 ] || fail "GET /auth/me through the BFF"
ok "authenticated as $(json "d['email'] + ' (' + d['role'] + ')'")"

[ "$(status GET "$WEB_URL/api/proxy/metrics")" = 404 ] || fail "BFF must not proxy /metrics"
ok "BFF does not expose /metrics"

[ "$(status GET "$WEB_URL/api/proxy/products?limit=5")" = 200 ] || fail "list products"
total=$(json "d['total']")
ok "products listed: $total"

if [ "$total" -gt 0 ]; then
  pid=$(json "d['items'][0]['id']")
  [ "$(mutate POST "$WEB_URL/api/proxy/forecast" --data "{\"product_id\":\"$pid\",\"horizon\":7}")" = 200 ] \
    || fail "forecast for product $pid"
  [ "$(json "len(d['points'])")" = 7 ] || fail "forecast should return 7 points"
  ok "forecast: method=$(json "d['method']"), 7 daily points with intervals"
else
  echo "  skip forecast (no products; run seed-demo or upload data)"
fi

if [ "${SMOKE_TRAIN:-0}" = 1 ]; then
  key="smoke-$(date +%s)-$$"
  code=$(mutate POST "$WEB_URL/api/proxy/train" -H "idempotency-key: $key" \
    --data '{"horizon":7,"max_epochs":1,"auto_promote":false}')
  [ "$code" = 202 ] || [ "$code" = 200 ] || fail "start training job (got $code)"
  jid=$(json "d['id']")
  for _ in $(seq 1 120); do
    status GET "$WEB_URL/api/proxy/train/$jid/status" >/dev/null
    st=$(json "d['status']")
    case "$st" in succeeded) break ;; failed|cancelled) fail "training job $jid ended $st: $(json "d.get('error')")" ;; esac
    sleep 5
  done
  [ "$st" = succeeded ] || fail "training job $jid still $st after 10 minutes"
  ok "training job ran on the worker and registered a model version"
fi

mutate POST "$WEB_URL/api/auth/logout" >/dev/null || true
[ "$(status GET "$WEB_URL/api/proxy/auth/me")" = 401 ] || fail "session should end after logout"
ok "logout ends the session"

echo "Smoke test passed ($pass checks)."
