"""Shared PostgreSQL DSN from bi/.env (falls back to docker-compose defaults)."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote_plus

from dotenv import load_dotenv

_ENV_LOADED = False


def load_bi_env() -> None:
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    _ENV_LOADED = True


def get_db_dsn() -> str:
    load_bi_env()
    user = os.environ.get("DB_USER", "edensign")
    password = os.environ.get("DB_PASSWORD", "edensign_dev")
    host = os.environ.get("DB_HOST", "localhost")
    port = os.environ.get("DB_PORT", "5432")
    db = os.environ.get("DB_NAME", "edensign_bi")
    return f"postgresql://{user}:{quote_plus(password)}@{host}:{port}/{db}"
