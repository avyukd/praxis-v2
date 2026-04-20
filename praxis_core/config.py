from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+asyncpg://praxis:praxis@localhost:5432/praxis"
    alembic_database_url: str = "postgresql://praxis:praxis@localhost:5432/praxis"

    vault_root: Path = Path.home() / "vault"
    inbox_root: Path = Path.home() / "praxis-inbox"
    claude_sessions_root: Path = Path("/tmp/praxis-claude-sessions")
    log_dir: Path = Path("/var/log/praxis")

    praxis_invoker: str = "cli"

    dispatcher_pool_size: int = 4
    dispatcher_tick_interval_s: float = 2.0
    worker_lease_s: int = 300
    worker_heartbeat_interval_s: int = 60
    worker_cancel_poll_interval_s: int = 5
    cli_no_event_timeout_s: int = 60
    cli_wall_clock_timeout_s: int = 600

    rate_limit_initial_backoff_s_min: int = 180
    rate_limit_initial_backoff_s_max: int = 300
    rate_limit_max_backoff_s: int = 3600

    priority_p0_min_pct: float = 0.40
    priority_p1_min_pct: float = 0.25
    priority_p2_min_pct: float = 0.15
    priority_p3_min_pct: float = 0.10
    priority_p4_min_pct: float = 0.10
    age_bump_after_min: int = 30

    sec_user_agent: str = "praxis-v2 research-admin@praxis.local"
    edgar_poll_interval_s: int = 60
    edgar_form_types: str = "8-K"
    edgar_search_days_back: int = 2
    edgar_item_allowlist: str = (
        "1.01,2.01,2.02,2.03,2.04,2.05,2.06,3.02,4.01,4.02,5.01,5.02,5.06,7.01,8.01"
    )
    market_cap_max_usd: int = 2_000_000_000
    market_cap_cache_ttl_s: int = 86_400
    cik_ticker_refresh_interval_s: int = 86_400

    ntfy_base_url: str = "https://ntfy.sh"
    ntfy_alert_topic: str = "praxis-alerts"
    ntfy_signal_topic: str = "praxis-signals"

    restic_repository: str = ""
    restic_password_file: str = ""
    restic_snapshot_interval_s: int = 3600

    dashboard_host: str = "0.0.0.0"
    dashboard_port: int = 8080

    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8765

    market_open_et: str = "08:00"
    market_close_et: str = "16:00"
    market_timezone: str = "America/New_York"

    @property
    def edgar_form_types_list(self) -> list[str]:
        return [s.strip() for s in self.edgar_form_types.split(",") if s.strip()]

    @property
    def edgar_item_allowlist_set(self) -> set[str]:
        return {s.strip() for s in self.edgar_item_allowlist.split(",") if s.strip()}


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
