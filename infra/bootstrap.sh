#!/usr/bin/env bash
# One-time bootstrap for the Ryzen box (Ubuntu 24.04 LTS assumed).
# Run with sudo.
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "must be run as root (sudo)" >&2
  exit 1
fi

REPO_URL="${REPO_URL:-https://github.com/your-org/praxis-v2.git}"
INSTALL_ROOT="${INSTALL_ROOT:-/opt/praxis-v2}"
PRAXIS_USER="${PRAXIS_USER:-praxis}"
VAULT_DIR="${VAULT_DIR:-/home/${PRAXIS_USER}/vault}"
INBOX_DIR="${INBOX_DIR:-/home/${PRAXIS_USER}/praxis-inbox}"
ETC_DIR="/etc/praxis"

echo "[bootstrap] installing apt packages"
apt update
DEBIAN_FRONTEND=noninteractive apt install -y \
  git curl build-essential \
  postgresql-16 postgresql-contrib \
  caddy \
  syncthing \
  restic \
  tmux htop jq

echo "[bootstrap] creating praxis user"
if ! id -u "${PRAXIS_USER}" >/dev/null 2>&1; then
  useradd -m -s /bin/bash "${PRAXIS_USER}"
fi

echo "[bootstrap] installing uv (Python package manager)"
su - "${PRAXIS_USER}" -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'

echo "[bootstrap] installing claude CLI (Max subscription; user must log in separately)"
su - "${PRAXIS_USER}" -c 'curl -fsSL https://claude.ai/install.sh | bash' || echo "[bootstrap] claude CLI install skipped — install manually if needed"

echo "[bootstrap] cloning repo"
if [[ ! -d "${INSTALL_ROOT}/.git" ]]; then
  git clone "${REPO_URL}" "${INSTALL_ROOT}"
fi
chown -R "${PRAXIS_USER}:${PRAXIS_USER}" "${INSTALL_ROOT}"

echo "[bootstrap] setting up Python env"
su - "${PRAXIS_USER}" -c "cd ${INSTALL_ROOT} && ~/.local/bin/uv sync --no-dev"

echo "[bootstrap] creating directories"
mkdir -p "${VAULT_DIR}" "${INBOX_DIR}" "${ETC_DIR}"
chown -R "${PRAXIS_USER}:${PRAXIS_USER}" "${VAULT_DIR}" "${INBOX_DIR}"
chown -R root:root "${ETC_DIR}"
chmod 750 "${ETC_DIR}"

echo "[bootstrap] preparing env file"
if [[ ! -f "${ETC_DIR}/praxis.env" ]]; then
  cp "${INSTALL_ROOT}/.env.example" "${ETC_DIR}/praxis.env"
  chmod 640 "${ETC_DIR}/praxis.env"
  chown root:"${PRAXIS_USER}" "${ETC_DIR}/praxis.env"
  echo "[bootstrap] ${ETC_DIR}/praxis.env written from .env.example — edit it now with real values"
fi

echo "[bootstrap] configuring Postgres"
sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='praxis'" | grep -q 1 || \
  sudo -u postgres psql -c "CREATE ROLE praxis WITH LOGIN PASSWORD 'praxis'"
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='praxis'" | grep -q 1 || \
  sudo -u postgres createdb -O praxis praxis

echo "[bootstrap] running migrations"
su - "${PRAXIS_USER}" -c "cd ${INSTALL_ROOT} && ~/.local/bin/uv run alembic upgrade head"

echo "[bootstrap] installing systemd units"
cp "${INSTALL_ROOT}/infra/systemd/"*.service /etc/systemd/system/
systemctl daemon-reload

echo "[bootstrap] enabling services"
for svc in \
  praxis-dispatcher \
  praxis-scheduler \
  praxis-mcp \
  praxis-dashboard \
  praxis-poller-edgar-8k \
  praxis-poller-inbox \
  praxis-syncer; do
  systemctl enable "${svc}.service"
done

echo ""
echo "[bootstrap] DONE"
echo ""
echo "Next steps:"
echo "  1. Edit ${ETC_DIR}/praxis.env — set DATABASE_URL, VAULT_ROOT=${VAULT_DIR}, INBOX_ROOT=${INBOX_DIR}, NTFY_* topics, SEC_USER_AGENT"
echo "  2. Initialize restic repo: sudo -u ${PRAXIS_USER} restic -r <repo> init"
echo "  3. Seed vault: sudo -u ${PRAXIS_USER} cp ${INSTALL_ROOT}/vault_seed/* ${VAULT_DIR}/"
echo "  4. Log in to Claude CLI: sudo -u ${PRAXIS_USER} claude login"
echo "  5. Start services: systemctl start 'praxis-*.service'"
echo "  6. Dashboard: curl localhost:8080/ or open in browser"
