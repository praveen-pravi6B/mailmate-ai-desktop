"""One-time OAuth setup for MailMate AI Desktop.

Run this script once to authorise both Gmail (read-only) and Google
Calendar (event creation) with a single browser consent flow.

Usage:
    python auth_setup.py

This creates token.json in the project root.  Never commit that file.
"""

import os
import sys

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]

CREDENTIALS_PATH = os.environ.get("GOOGLE_CREDENTIALS_PATH", "credentials.json")
TOKEN_PATH = os.environ.get("GOOGLE_TOKEN_PATH", "token.json")


def main():
    if not os.path.exists(CREDENTIALS_PATH):
        print(f"ERROR: {CREDENTIALS_PATH} not found.")
        print(
            "Download it from Google Cloud Console:\n"
            "  APIs & Services → Credentials → OAuth 2.0 Client IDs\n"
            "  → Download JSON → save as credentials.json in project root"
        )
        sys.exit(1)

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
    except ImportError:
        print("ERROR: Google auth libraries not installed.")
        print("Run: pip install google-auth-oauthlib google-auth-httplib2")
        sys.exit(1)

    creds = None
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("Refreshing existing token…")
            creds.refresh(Request())
        else:
            print("Opening browser for Google OAuth consent…")
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
        print(f"\n✅  token.json saved to: {TOKEN_PATH}")
    else:
        print(f"✅  Existing token is valid: {TOKEN_PATH}")

    print("\nScopes authorised:")
    for s in SCOPES:
        print(f"  • {s}")

    print("\nYou can now run the app:")
    print("  python -m app.ui.menu_bar")


if __name__ == "__main__":
    main()
