"""Load Supabase credentials from the environment.

The service role key bypasses row-level security. Keep it in a local .env file
or in GitHub Actions secrets, and never commit it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL_ENV = "SUPABASE_URL"
SUPABASE_SERVICE_KEY_ENV = "SUPABASE_SERVICE_KEY"


@dataclass(frozen=True)
class Settings:
    supabase_url: str
    supabase_service_key: str


def load_settings() -> Settings:
    """Return Supabase settings or raise if either secret is missing."""
    supabase_url = os.environ.get(SUPABASE_URL_ENV, "").strip()
    supabase_service_key = os.environ.get(SUPABASE_SERVICE_KEY_ENV, "").strip()

    missing = [
        name
        for name, value in (
            (SUPABASE_URL_ENV, supabase_url),
            (SUPABASE_SERVICE_KEY_ENV, supabase_service_key),
        )
        if not value
    ]
    if missing:
        joined = ", ".join(missing)
        raise RuntimeError(
            f"Missing required environment variable(s): {joined}. "
            "Set them in a local .env file or in the GitHub Actions secrets."
        )

    return Settings(
        supabase_url=supabase_url,
        supabase_service_key=supabase_service_key,
    )
