#!/usr/bin/env python3
"""One-time local helper to mint a Google refresh token for the bot account.

Alternative to the /google/oauth/* web flow — use this when you can run Python
on a machine with a browser. Log in as the DEDICATED BOT Google account when the
browser opens.

    pip install google-auth-oauthlib
    python scripts/google_oauth.py /path/to/oauth_client.json

Prints GOOGLE_REFRESH_TOKEN (+ client id/secret) to paste into Railway env.
The oauth_client.json is the "Desktop app" (or "Web app") OAuth client download.
"""

import json
import sys

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive.file",
]


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: python scripts/google_oauth.py <oauth_client.json>")
        raise SystemExit(1)
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(sys.argv[1], scopes=SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    conf = json.load(open(sys.argv[1], encoding="utf-8"))
    block = conf.get("installed") or conf.get("web") or {}
    print("\n=== paste into Railway env ===")
    print("GOOGLE_CLIENT_ID=" + block.get("client_id", ""))
    print("GOOGLE_CLIENT_SECRET=" + block.get("client_secret", ""))
    print("GOOGLE_REFRESH_TOKEN=" + (creds.refresh_token or "(none — re-run, ensure prompt=consent)"))


if __name__ == "__main__":
    main()
