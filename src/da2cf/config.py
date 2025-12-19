from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from dotenv import load_dotenv


# Load environment from a .env file at the project root, if present.
_BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(_BASE_DIR / ".env")


def _csv_env(name: str, default: str | None = None) -> List[str]:
    raw = os.getenv(name, default or "")
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class EnvConfig:
    directadmin_base_url: str | None = os.getenv("DIRECTADMIN_BASE_URL")
    directadmin_username: str | None = os.getenv("DIRECTADMIN_USERNAME")
    directadmin_password: str | None = os.getenv("DIRECTADMIN_PASSWORD")
    directadmin_token: str | None = os.getenv("DIRECTADMIN_TOKEN")

    cloudflare_email: str | None = os.getenv("CLOUDFLARE_EMAIL")
    cloudflare_api_key: str | None = os.getenv("CLOUDFLARE_GLOBAL_API_KEY")

    app_admin_user: str | None = os.getenv("APP_ADMIN_USER")
    app_admin_password: str | None = os.getenv("APP_ADMIN_PASSWORD")
    app_secret_key: str | None = os.getenv("APP_SECRET_KEY")

    default_sync_interval_minutes: int = int(os.getenv("DEFAULT_SYNC_INTERVAL_MINUTES", "15"))
    managed_record_types: List[str] = field(
        default_factory=lambda: _csv_env(
            "MANAGED_RECORD_TYPES", "A,AAAA,CNAME,TXT,MX,SRV,CAA"
        )
    )
    exclude_names: List[str] = field(
        default_factory=lambda: _csv_env("EXCLUDE_NAMES", "")
    )
    sync_concurrency: int = int(os.getenv("SYNC_CONCURRENCY", "5"))

    proxy_a: bool = _bool_env("PROXY_A", True)
    proxy_aaaa: bool = _bool_env("PROXY_AAAA", True)
    proxy_cname: bool = _bool_env("PROXY_CNAME", True)
    proxy_host: bool = _bool_env("PROXY_HOST", True)
    proxy_sub: bool = _bool_env("PROXY_SUB", True)

    acme_sync_interval_minutes: int = int(
        os.getenv("ACME_SYNC_INTERVAL_MINUTES", "5")
    )


env_config = EnvConfig()
