#!/usr/bin/env bash
# Preflight checklist for production start (Section G D70).
# Exits 0 if all checks pass; nonzero + diagnostic otherwise.
set -uo pipefail

REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$REPO_ROOT"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'

fail=0
ok() { echo -e "${GREEN}[ok]${NC} $1"; }
bad() { echo -e "${RED}[FAIL]${NC} $1"; fail=1; }
warn() { echo -e "${YELLOW}[warn]${NC} $1"; }

echo "=== praxis-v2 preflight ==="

[[ -f .env ]] && ok ".env present" || { bad ".env missing"; exit 1; }

# Load env
set -a
# shellcheck source=/dev/null
source .env
set +a

[[ -n "${DATABASE_URL:-}" ]] && ok "DATABASE_URL set" || bad "DATABASE_URL missing"
[[ -n "${ALEMBIC_DATABASE_URL:-}" ]] && ok "ALEMBIC_DATABASE_URL set" || bad "ALEMBIC_DATABASE_URL missing"
[[ -n "${SEC_USER_AGENT:-}" ]] && ok "SEC_USER_AGENT set" || bad "SEC_USER_AGENT missing"
[[ -n "${NTFY_SIGNAL_TOPIC:-}" ]] && ok "NTFY_SIGNAL_TOPIC set ($NTFY_SIGNAL_TOPIC)" || bad "NTFY_SIGNAL_TOPIC missing"
[[ -n "${NTFY_ALERT_TOPIC:-}" ]] && ok "NTFY_ALERT_TOPIC set ($NTFY_ALERT_TOPIC)" || bad "NTFY_ALERT_TOPIC missing"

# Vault paths
for var in VAULT_ROOT INBOX_ROOT CLAUDE_SESSIONS_ROOT; do
  path="${!var:-}"
  if [[ -z "$path" ]]; then
    bad "$var missing"
  elif [[ -d "$path" ]]; then
    ok "$var=$path (exists)"
  else
    warn "$var=$path (does not exist; will be created on first write)"
  fi
done

# Postgres reachable
if command -v psql >/dev/null 2>&1; then
  if psql "$ALEMBIC_DATABASE_URL" -c "SELECT 1" >/dev/null 2>&1; then
    ok "Postgres reachable"
  else
    bad "Postgres connection failed"
  fi
else
  warn "psql not installed (Postgres check skipped)"
fi

# Alembic up to date
export PATH="$HOME/.local/bin:$PATH"
if command -v uv >/dev/null 2>&1; then
  head_rev=$(uv run alembic heads 2>&1 | grep -oE '[0-9]{4}_[a-z_]+' | head -1 || true)
  db_rev=$(uv run alembic current 2>&1 | grep -oE '[0-9]{4}_[a-z_]+' | head -1 || true)
  if [[ "$head_rev" == "$db_rev" ]] && [[ -n "$head_rev" ]]; then
    ok "Alembic current == head ($head_rev)"
  else
    bad "Alembic drift: head=$head_rev, db=$db_rev — run 'uv run alembic upgrade head'"
  fi
else
  bad "uv not on PATH"
fi

# Claude CLI
if command -v claude >/dev/null 2>&1; then
  ok "claude CLI at $(command -v claude)"
  claude_version=$(claude --version 2>&1 | head -1 || true)
  echo "     $claude_version"
else
  bad "claude CLI not found on PATH"
fi

# ntfy reachable
if command -v curl >/dev/null 2>&1; then
  http_code=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 5 "${NTFY_BASE_URL:-https://ntfy.sh}" || echo "000")
  if [[ "$http_code" =~ ^[23] ]]; then
    ok "ntfy reachable ($NTFY_BASE_URL → HTTP $http_code)"
  else
    warn "ntfy check returned HTTP $http_code (non-fatal)"
  fi
fi

echo "==="
if [[ $fail -eq 0 ]]; then
  echo -e "${GREEN}Preflight PASS${NC}"
  exit 0
else
  echo -e "${RED}Preflight FAIL — fix above before starting services${NC}"
  exit 1
fi
