#!/usr/bin/env bash
# Render vault/.mcp-config.json from the template, substituting install paths.
# Can be run anytime after the vault and repo exist (re-runnable).
set -euo pipefail

REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
VAULT_ROOT="${VAULT_ROOT:-${HOME}/vault}"
UV_BIN="${UV_BIN:-$(command -v uv || echo /usr/local/bin/uv)}"

TEMPLATE="${REPO_ROOT}/vault_seed/.mcp-config.json.template"
TARGET="${VAULT_ROOT}/.mcp-config.json"

if [[ ! -f "$TEMPLATE" ]]; then
  echo "template not found: $TEMPLATE" >&2
  exit 1
fi

mkdir -p "$(dirname "$TARGET")"
sed \
  -e "s|__REPO_ROOT__|${REPO_ROOT}|g" \
  -e "s|__UV_BIN__|${UV_BIN}|g" \
  "$TEMPLATE" > "$TARGET"

echo "[render_mcp_config] wrote $TARGET"
echo "   REPO_ROOT=$REPO_ROOT"
echo "   UV_BIN=$UV_BIN"
