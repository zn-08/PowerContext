"""Runtime configuration for the SDD context module.

Every value can be overridden through the environment so the module can run
against a judge-owned seekdb instance and a judge-owned PowerContext server
without editing any file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_SEEKDB_DATABASE = "minimax_sdd"
DEFAULT_POWERCONTEXT_URL = "http://127.0.0.1:8787"
DEFAULT_POWERCONTEXT_SCOPE = "minimax-sdd"


def _env(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value if value else default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from error


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if not raw:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    """Resolved settings for one context-module run."""

    seekdb_host: str = field(default_factory=lambda: _env("SEEKDB_HOST", "127.0.0.1"))
    seekdb_port: int = field(default_factory=lambda: _env_int("SEEKDB_PORT", 2881))
    seekdb_user: str = field(default_factory=lambda: _env("SEEKDB_USER", "root"))
    seekdb_password: str = field(default_factory=lambda: _env("SEEKDB_PASSWORD", ""))
    seekdb_database: str = field(default_factory=lambda: _env("SEEKDB_DATABASE", DEFAULT_SEEKDB_DATABASE))

    powercontext_url: str = field(default_factory=lambda: _env("POWERCONTEXT_URL", DEFAULT_POWERCONTEXT_URL))
    powercontext_scope: str = field(default_factory=lambda: _env("POWERCONTEXT_SCOPE", DEFAULT_POWERCONTEXT_SCOPE))
    powercontext_token: str = field(default_factory=lambda: _env("POWERCONTEXT_TOKEN", ""))

    minimax_api_key: str = field(default_factory=lambda: _env("MINIMAX_API_KEY", ""))
    minimax_base_url: str = field(default_factory=lambda: _env("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1"))
    minimax_model: str = field(default_factory=lambda: _env("MINIMAX_MODEL", "MiniMax-M3.1-Flash"))

    workspace: Path = field(default_factory=lambda: Path(_env("MINIMAX_WORKSPACE", ".")).resolve())
    log_sql: bool = field(default_factory=lambda: _env_bool("MINIMAX_LOG_SQL", True))

    @property
    def database_url(self) -> str:
        return f"mysql+pymysql://{self.seekdb_user}@{self.seekdb_host}:{self.seekdb_port}/{self.seekdb_database}"


__all__ = [
    "DEFAULT_POWERCONTEXT_SCOPE",
    "DEFAULT_POWERCONTEXT_URL",
    "DEFAULT_SEEKDB_DATABASE",
    "Settings",
]
