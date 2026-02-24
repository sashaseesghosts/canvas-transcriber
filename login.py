"""
Canvas session management.

Handles loading/saving Playwright browser cookies and waiting for the user
to complete SSO/MFA login in the non-headless browser window.
"""

import json
import time
from pathlib import Path


def load_session(context, session_file: Path) -> bool:
    """Load session cookies from *session_file* into *context*.

    Returns True on success, False if the file is missing or malformed.
    """
    try:
        with open(session_file, "r") as f:
            cookies = json.load(f)
        context.add_cookies(cookies)
        return True
    except Exception as e:
        print(f"Could not load session: {e}")
        return False


def save_session(context, session_file: Path) -> None:
    """Persist the current browser cookies to *session_file*."""
    cookies = context.cookies()
    with open(session_file, "w") as f:
        json.dump(cookies, f)
    print(f"Session saved to {session_file}")


def wait_for_canvas_login(page, timeout: int = 180) -> bool:
    """Block until the browser lands on an authenticated Canvas course page.

    Polls every 2 seconds.  When it detects a login/SSO page it prompts the
    user to complete authentication and press Enter.

    Returns True if login succeeded, False if *timeout* was reached or the
    user chose to quit.
    """
    print("\n--- Waiting for Canvas login ---")
    start = time.time()

    while time.time() - start < timeout:
        current_url = page.url
        page_title = page.title()

        on_canvas = "instructure.com/courses/" in current_url.lower()
        not_login_title = "login" not in page_title.lower()
        has_canvas_dom = page.evaluate(
            "() => document.querySelector('#content, .user_content, .ic-app') !== null"
        )

        if on_canvas and (not_login_title or has_canvas_dom):
            print(f"Logged in — {page_title}")
            return True

        on_login_page = any(
            k in current_url.lower() for k in ("sso", "login", "saml")
        )
        if on_login_page:
            remaining = int(timeout - (time.time() - start))
            try:
                answer = input(
                    f"Complete SSO/MFA in the browser, then press Enter "
                    f"(or 'q' to quit) [{remaining}s left]: "
                ).strip().lower()
            except EOFError:
                answer = ""
            if answer == "q":
                return False

        time.sleep(2)

    print(f"Login timed out after {timeout}s.")
    return False
