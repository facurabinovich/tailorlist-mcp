"""The guided setup text: one source for the setup web page and for the text the tool returns.

Inline markup in the details is minimal: **bold** and `code`.
"""
from __future__ import annotations

import re

DASHBOARD_URL = "https://developer.spotify.com/dashboard"


def steps(redirect_uri: str) -> list[dict]:
    """Step 0 is the introduction; steps 1..7 are the numbered actions."""
    return [
        {
            "title": "Before you start",
            "detail": (
                "The Spotify account that creates the app needs **Spotify Premium** (Spotify requires it "
                "for developer apps). Registering is free, takes about 3 minutes, and is done once. "
                "Nothing goes through a third-party server: your app, your login, your computer."
            ),
        },
        {
            "title": "Open the Spotify developer dashboard",
            "detail": f"Open {DASHBOARD_URL} and log in with your Spotify account. Accept the developer terms if asked.",
        },
        {
            "title": "Create an app",
            "detail": (
                'Click "Create app". App name: anything, e.g. "My Tailorlist" (the name must not contain '
                'the word "Spotify"). App description: anything, e.g. "Personal playlist organizer".'
            ),
        },
        {
            "title": "Add the Redirect URI",
            "detail": (
                f'Under Redirect URIs, paste exactly `{redirect_uri}` and click "Add". It must match '
                "character by character (`127.0.0.1`, not `localhost`; `http`, not `https`; no trailing slash)."
            ),
        },
        {
            "title": "Choose the Web API",
            "detail": (
                'Under "Which API/SDKs are you planning to use?" tick "Web API". Accept the terms and '
                'click "Save".'
            ),
        },
        {
            "title": "Copy the Client ID",
            "detail": (
                'Open the app\'s "Settings" and copy the **Client ID** (32 letters and digits). You do NOT '
                "need the client secret; never share that one."
            ),
        },
        {
            "title": "Give the Client ID to Tailorlist",
            "detail": "Paste it in the setup page, or tell it to the assistant in the chat.",
        },
        {
            "title": "Approve access in Spotify",
            "detail": (
                "Spotify then asks you to approve access (read your playlists and library, create and edit "
                "playlists). Approve, and you are connected."
            ),
        },
    ]


def troubleshooting() -> list[str]:
    return [
        'Spotify says "INVALID_CLIENT: Invalid redirect URI": the Redirect URI in your app does not match '
        "exactly. Fix it in the app's Settings.",
        'Spotify says "INVALID_CLIENT: Invalid client": the Client ID was copied wrong. Copy it again from '
        "the app's Settings.",
        'A "user not registered" or 403 message after logging in: in the app\'s Settings open "User '
        'Management" and add the name and email of the Spotify account you log in with. An app in '
        "Development Mode allows up to 5 people; the owner needs Premium.",
        "The port is already in use: another program is using it. Close that program, or set "
        "SPOTIFY_REDIRECT_URI to another port and register that URI in the app.",
    ]


def as_text(redirect_uri: str) -> str:
    lines: list[str] = []
    for i, step in enumerate(steps(redirect_uri)):
        label = step["title"] if i == 0 else f"{i}. {step['title']}"
        lines.append(f"{label}\n   {step['detail']}")
    lines.append("If something goes wrong:")
    lines.extend(f"- {item}" for item in troubleshooting())
    return "\n".join(lines)


def valid_client_id(text: str | None) -> str | None:
    """The Client ID if `text` is one (32 hex characters, spaces and quotes ignored), else None."""
    cleaned = (text or "").strip().strip("\"'").strip()
    if re.fullmatch(r"[0-9a-fA-F]{32}", cleaned):
        return cleaned.lower()
    return None
