#!/usr/bin/env python3
"""Validate env before deploy. Exit 0 if OK."""
from __future__ import annotations

import os
import sys


def main() -> int:
    required = ["TELEGRAM_BOT_TOKEN", "OPENAI_API_KEY", "ALLOWED_USER_IDS"]
    missing = [k for k in required if not os.environ.get(k, "").strip()]
    if missing:
        print("Missing:", ", ".join(missing))
        print("Copy .env.example → .env (local) or set Railway Variables.")
        return 1
    ids = [x.strip() for x in os.environ["ALLOWED_USER_IDS"].split(",") if x.strip()]
    if len(ids) < 1:
        print("ALLOWED_USER_IDS needs at least one Telegram user id")
        return 1
    if not os.environ.get("PUBLIC_URL") and os.environ.get("RAILWAY_ENVIRONMENT"):
        print("Warning: PUBLIC_URL empty on Railway — webhook will not register.")
    print("Env looks good.")
    print("  model:", os.environ.get("OPENAI_MODEL", "gpt-4o"))
    print("  allowed users:", len(ids))
    print("  public_url:", os.environ.get("PUBLIC_URL") or "(polling)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
