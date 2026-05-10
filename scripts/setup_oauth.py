"""Interactive OAuth setup for any Google scope (Drive, Calendar, Gmail).

Run this LOCALLY on your Mac (it opens a browser for you to authorize).
The script reuses your existing GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET
from .env (or accepts them as args). At the end it prints a single line
to paste into the server's .env.

Usage:
    python -m scripts.setup_oauth drive
    python -m scripts.setup_oauth calendar
    python -m scripts.setup_oauth gmail
    python -m scripts.setup_oauth drive calendar  # multiple scopes at once
"""
from __future__ import annotations

import sys
from pathlib import Path


SCOPES = {
    "drive": "https://www.googleapis.com/auth/drive.file",
    "calendar": "https://www.googleapis.com/auth/calendar",
    "gmail.readonly": "https://www.googleapis.com/auth/gmail.readonly",
    "gmail.send": "https://www.googleapis.com/auth/gmail.send",
    "gmail.modify": "https://www.googleapis.com/auth/gmail.modify",
    "gmail": [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/gmail.modify",
    ],
}


ENV_VAR_FOR = {
    "drive": "GDRIVE",
    "calendar": "GCAL",
    "gmail": "GMAIL",
    "gmail.readonly": "GMAIL",
    "gmail.send": "GMAIL",
    "gmail.modify": "GMAIL",
}


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)

    requested = sys.argv[1:]
    flat_scopes: list[str] = []
    env_prefixes: set[str] = set()
    for r in requested:
        if r not in SCOPES:
            print(f"Unknown scope group: {r}")
            print("Available:", ", ".join(SCOPES.keys()))
            sys.exit(1)
        scope = SCOPES[r]
        if isinstance(scope, list):
            flat_scopes.extend(scope)
        else:
            flat_scopes.append(scope)
        env_prefixes.add(ENV_VAR_FOR[r])

    # Load credentials from local .env (the same file the bot uses).
    from dotenv import load_dotenv
    import os

    env_path = Path(".env")
    if env_path.exists():
        load_dotenv(env_path)

    client_id = os.environ.get("GMAIL_CLIENT_ID") or input("Google CLIENT_ID: ").strip()
    client_secret = os.environ.get("GMAIL_CLIENT_SECRET") or input("Google CLIENT_SECRET: ").strip()
    if not client_id or not client_secret:
        print("Need CLIENT_ID and CLIENT_SECRET.")
        sys.exit(1)

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("Missing dependency. Install with:")
        print("    pip install google-auth-oauthlib")
        sys.exit(1)

    config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }

    print(f"\nRequesting scopes: {flat_scopes}")
    print("→ A browser window will open. Authorize → confirm. Then come back.\n")

    flow = InstalledAppFlow.from_client_config(config, scopes=flat_scopes)
    creds = flow.run_local_server(
        port=0,
        prompt="consent",
        access_type="offline",
        include_granted_scopes="true",
    )

    if not creds.refresh_token:
        print("\n❌ No refresh_token returned. This usually means the OAuth")
        print("   client already had consent — revoke at https://myaccount.google.com/permissions")
        print("   and re-run.")
        sys.exit(1)

    print("\n✅ Got refresh token. Add THIS to your server's .env:\n")
    for prefix in sorted(env_prefixes):
        print(f"{prefix}_CLIENT_ID={client_id}")
        print(f"{prefix}_CLIENT_SECRET={client_secret}")
        print(f"{prefix}_REFRESH_TOKEN={creds.refresh_token}")
    print("\nThen SSH to the server:")
    print("    ssh root@46.224.151.102 'cd /opt/omnime && docker compose restart omnime'")


if __name__ == "__main__":
    main()
