from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="PGWEB_")

    # Holds the app's own SQLite DB (saved connections) and, when PGWEB_SECRET_KEY is not set,
    # the auto-generated master key.
    data_dir: Path = Path("./data")
    # Fernet key (urlsafe base64, 32 bytes). Encrypts stored passwords; JWT signing key is derived from it.
    secret_key: str = ""
    # Login sessions: opaque token in an HttpOnly cookie, revocable server-side.
    session_ttl_days: int = 7  # absolute lifetime
    session_idle_minutes: int = 480  # signed out after this long without a request
    login_attempt_window_seconds: int = 900
    max_login_failures_per_ip_email: int = 5
    max_login_failures_per_ip: int = 30
    max_login_rate_entries: int = 10_000
    # Optional first admin, created on startup only while no users exist (handy for Docker).
    bootstrap_admin_email: str = ""
    bootstrap_admin_password: str = ""
    # Manual transactions pin a DB connection, so they are bounded.
    tx_idle_timeout_seconds: int = 600
    tx_max_age_seconds: int = 3600
    max_open_tx_per_user: int = 3
    audit_retention_days: int = 90
    # Set false only for plain-http local dev (Safari won't store Secure cookies on http://localhost).
    cookie_secure: bool = True
    # Optional fnmatch patterns limiting which DB hosts can be registered, e.g. ["*.rds.amazonaws.com"].
    allowed_hosts: list[str] = []
    cors_origins: list[str] = ["http://localhost:5173"]

    @property
    def database_url(self) -> str:
        return f"sqlite+aiosqlite:///{(self.data_dir / 'pgweb2.db').resolve()}"

    pool_min_size: int = 0
    pool_max_size: int = 5
    statement_timeout_ms: int = 30_000


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
        _settings.data_dir.mkdir(parents=True, exist_ok=True)
    return _settings
