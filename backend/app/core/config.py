"""Validated application settings.

The process refuses to start on a missing or malformed required variable
rather than defaulting silently. See ARCHITECTURE.md §11.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, RedisDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Application -------------------------------------------------------
    app_name: str = "Jerry MLB Prediction Lab"
    environment: Literal["development", "test", "production"] = "development"
    debug: bool = False
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "console"] = "json"
    git_sha: str = "unknown"

    # --- Storage -----------------------------------------------------------
    database_url: PostgresDsn = Field(
        default="postgresql+psycopg://postgres@localhost:5432/jerry_mlb",  # type: ignore[arg-type]
        description="PostgreSQL DSN (SQLAlchemy psycopg3 driver).",
    )
    db_pool_size: int = Field(default=5, ge=1, le=50)
    db_max_overflow: int = Field(default=10, ge=0, le=50)
    db_echo: bool = False

    redis_url: RedisDsn | None = Field(
        default="redis://localhost:6379/0",  # type: ignore[arg-type]
        description="Redis DSN. When unset, caching is disabled (not faked).",
    )
    cache_enabled: bool = True

    # --- API ---------------------------------------------------------------
    api_prefix: str = "/api/v1"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    admin_api_key: str | None = Field(
        default=None,
        description="When unset, admin/diagnostics write routes are disabled.",
    )

    # --- Providers ---------------------------------------------------------
    schedule_provider: str = "mlb_statsapi"
    results_provider: str = "mlb_statsapi"
    reference_provider: str = "mlb_statsapi"
    lineup_provider: str | None = None
    statcast_provider: str | None = None
    weather_provider: str | None = None
    injury_provider: str | None = None
    park_factor_provider: str | None = None
    odds_provider: str | None = None

    mlb_statsapi_base_url: str = "https://statsapi.mlb.com/api/v1"
    mlb_statsapi_timeout_s: float = Field(default=30.0, gt=0)
    mlb_statsapi_min_interval_ms: int = Field(
        default=120, ge=0, description="Minimum spacing between outbound requests."
    )
    mlb_statsapi_max_retries: int = Field(default=4, ge=0, le=10)

    # --- Modeling ----------------------------------------------------------
    active_model_name: str = "jerry_logistic"
    feature_set_version: str = "fs_v1"
    model_artifact_dir: str = "artifacts/models"
    prediction_as_of_policy: Literal["T_MINUS_3H", "T_MINUS_60M", "T_MINUS_15M"] = "T_MINUS_3H"
    random_seed: int = 20240401

    # Training / backtest guardrails (BACKTEST_PLAN.md §1)
    min_train_rows: int = Field(default=500, ge=50)
    backtest_validation_days: int = Field(default=45, ge=7)
    min_team_games_for_prediction: int = Field(default=10, ge=0)

    # --- Ingestion ---------------------------------------------------------
    schedule_window_days_back: int = Field(default=3, ge=0)
    schedule_window_days_forward: int = Field(default=10, ge=0)
    raw_payload_retention_days: int = Field(default=90, ge=1)
    store_raw_payloads: bool = Field(
        default=True,
        description=(
            "Persist the verbatim provider response alongside the normalized "
            "rows. Worth ~56 KB per game and roughly three quarters of the "
            "database. Turn off for a historical backfill over a network "
            "connection, where it dominates the write cost and buys little: a "
            "past game's payload can be refetched from a stable public API at "
            "any time. Leave on for the daily refresh, where the volume is "
            "trivial and the audit value is highest."
        ),
    )

    # --- Observability -----------------------------------------------------
    sentry_dsn: str | None = None

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @field_validator(
        "redis_url", "admin_api_key", "sentry_dsn", "lineup_provider",
        "statcast_provider", "weather_provider", "injury_provider",
        "park_factor_provider", "odds_provider",
        mode="before",
    )
    @classmethod
    def _blank_is_unset(cls, v: object) -> object:
        """An empty value means "not configured", not a malformed one.

        Container orchestrators routinely pass VAR= for an unset variable; a
        blank optional provider must disable that category rather than crash the
        process on startup.
        """
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator("database_url", mode="before")
    @classmethod
    def _ensure_psycopg_driver(cls, v: object) -> object:
        """Accept the DSN a managed Postgres provider actually hands out.

        Render, Heroku, Railway, Supabase and Neon all issue `postgresql://` or
        the legacy `postgres://`. SQLAlchemy reads a bare `postgresql://` as
        "use psycopg2", which is not installed here, so the process would die on
        first connect with a bare ModuleNotFoundError — a long way from the
        actual cause. Pinning the driver is unambiguous and costs nothing, so do
        it rather than making a copy-paste of a provider's URL a mistake.
        """
        if isinstance(v, str):
            dsn = v.strip()
            for prefix in ("postgresql://", "postgres://"):
                if dsn.startswith(prefix):
                    return "postgresql+psycopg://" + dsn[len(prefix) :]
        return v

    @property
    def sqlalchemy_url(self) -> str:
        return str(self.database_url)

    @property
    def caching_active(self) -> bool:
        return self.cache_enabled and self.redis_url is not None

    def __repr__(self) -> str:  # pragma: no cover - trivial
        redacted = {"admin_api_key", "sentry_dsn", "database_url", "redis_url"}
        parts = [
            f"{k}={'***' if k in redacted and getattr(self, k) else getattr(self, k)!r}"
            for k in ("app_name", "environment", "log_level", "database_url", "redis_url",
                      "admin_api_key", "active_model_name", "feature_set_version")
        ]
        return f"Settings({', '.join(parts)})"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
