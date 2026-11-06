"""Process-wide settings, read from the environment (prefix ``SIMP_``).

Secrets (WRDS password, BMLL key path) are read here and only here. They are never
logged, never returned by the API and never written to a run record.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SIMP_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://simp:simp@127.0.0.1:5432/simp"
    redis_url: str = "redis://127.0.0.1:6379/0"
    lake_root: Path = Path(__file__).resolve().parents[1] / "lake"

    # Where market and CRSP data come from when a pull is requested.
    #   synthetic: schema-identical generated data (no licence needed, labelled SYNTHETIC)
    #   live:      WRDS (CRSP CIZ) + BMLL lake partitions written by the BMLL puller
    data_source: Literal["synthetic", "live"] = "synthetic"
    synthetic_seed: int = 20260321

    # Job queue: "dramatiq" uses Redis workers; "inline" runs jobs in an API thread (dev / tests).
    queue: Literal["dramatiq", "inline"] = "dramatiq"

    # Auth: "dev" trusts the X-SIMP-User header against the allow-list; "oauth" uses GitHub OAuth.
    auth_mode: Literal["dev", "oauth"] = "dev"
    allowed_users: str = "tanish:researcher,andrew:viewer,admin:admin"
    session_secret: SecretStr = SecretStr("dev-only-change-me")
    oauth_client_id: str | None = None
    oauth_client_secret: SecretStr | None = None
    web_origin: str = "http://localhost:3000"

    # WRDS (CRSP). Standard WRDS env names are honoured too.
    wrds_username: str | None = Field(default=None, validation_alias="WRDS_USERNAME")
    wrds_password: SecretStr | None = Field(default=None, validation_alias="WRDS_PASSWORD")
    wrds_host: str = "wrds-pgdata.wharton.upenn.edu"
    wrds_port: int = 9737

    # BMLL SDK (outside the Lab) authenticates with an SSH key.
    bmll_key_path: Path | None = Field(default=None, validation_alias="BMLL_KEY_PATH")
    # T2 on live data: fetch missing Trades Plus minute bars during a run with the isolated puller.
    t2_auto_fetch: bool = True
    bmll_puller_dir: Path = Path(__file__).resolve().parents[1] / "connectors" / "bmll_puller"

    def allowed_user_roles(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for item in self.allowed_users.split(","):
            item = item.strip()
            if not item:
                continue
            name, _, role = item.partition(":")
            out[name.strip()] = (role or "viewer").strip()
        return out


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
