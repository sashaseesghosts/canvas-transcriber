import json
import sys
import time
from pathlib import Path

from login import load_session
from extractor import extract_links_from_page


CANVAS_URL = "https://<school>.instructure.com"
SESSION_FILE = Path("session.json")
OUTPUT_FILE = Path("links_output.json")
LOGIN_TIMEOUT = 180


def wait_for_canvas_page(page, target_url, timeout=LOGIN_TIMEOUT):
    """Wait for Canvas page to load after SSO login.

    Returns True if successfully reached Canvas page, False if timeout.
    """
    print("\n--- Waiting for Canvas page to load ---")
    print(f"Target URL: {target_url}")

    start_time = time.time()
    check_interval = 2

    while time.time() - start_time < timeout:
        current_url = page.url
        page_title = page.title()

        url_contains_courses = "instructure.com/courses/" in current_url.lower()
        title_not_gt_login = "gt login" not in page_title.lower()

        canvas_selector_exists = page.evaluate("""() => {
            return document.querySelector('#content') !== null || 
                   document.querySelector('.user_content') !== null ||
                   document.querySelector('.ic-app') !== null;
        }""")

        if url_contains_courses and (title_not_gt_login or canvas_selector_exists):
            print(f"\n--- Successfully reached Canvas page ---")
            print(f"Current URL: {current_url}")
            print(f"Page title: {page_title}")
            return True

        if (
            "sso" in current_url.lower()
            or "login" in current_url.lower()
            or "saml" in current_url.lower()
        ):
            print(f"\n--- Waiting for login ---")
            print(f"Current URL: {current_url}")
            print("Please complete SSO/MFA login in the browser...")
            print(
                f"Press Enter to check again now, or wait {int(timeout - (time.time() - start_time))}s for timeout."
            )

            user_input = (
                input("Press Enter when logged in, or 'q' to quit: ").strip().lower()
            )
            if user_input == "q":
                return False

        time.sleep(check_interval)

    print("\n--- TIMEOUT ---")
    print(f"Login did not complete within {timeout} seconds.")
    print(f"Current URL: {page.url}")
    print(f"Current title: {page.title()}")
    print("\nIf you need more time, you can:")
    print("  1. Increase LOGIN_TIMEOUT in main.py")
    print("  2. Log in faster and press Enter immediately after completing MFA")
    print("  3. Check if your session expired")
    return False


def save_session_cookie(context):
    cookies = context.cookies()
    with open(SESSION_FILE, "w") as f:
        json.dump(cookies, f)
    print(f"Session saved to {SESSION_FILE}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python main.py <canvas_course_page_url>")
        print(
            "Example: python main.py https://<school>.instructure.com/courses/123456/pages/module-1"
        )
        sys.exit(1)

    course_page_url = sys.argv[1]

    print("=" * 60)
    print("Canvas Lecture Transcriber - MVP")
    print("=" * 60)

    from playwright.sync_api import sync_playwright

    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()

    session_loaded = False
    if SESSION_FILE.exists():
        print(f"\nFound existing session: {SESSION_FILE}")
        load_response = input("Load existing session? (y/n): ").strip().lower()
        if load_response == "y":
            session_loaded = load_session(context, SESSION_FILE)
            if not session_loaded:
                print("Session expired or invalid. Please log in again.")

    print(f"\n--- Navigating to: {course_page_url} ---")
    page = context.new_page()
    page.goto(course_page_url)
    page.wait_for_load_state("domcontentloaded")

    current_url = page.url
    page_title = page.title()

    needs_login = (
        "login" in current_url.lower()
        or "sso" in current_url.lower()
        or "saml" in current_url.lower()
        or "gt login" in page_title.lower()
    )

    if needs_login:
        print("\n--- Login Required ---")
        print(f"Current URL: {current_url}")
        print(f"Page title: {page_title}")
        print("\nPlease complete SSO/MFA login manually in the browser.")
        print("The script will wait for you to finish authentication.")

        success = wait_for_canvas_page(page, course_page_url)

        if not success:
            print("\nExiting due to timeout.")
            browser.close()
            playwright.stop()
            sys.exit(1)

        print("Login successful! Saving session...")
        save_session_cookie(context)
    else:
        print(f"\n--- Already authenticated ---")
        print(f"Current URL: {current_url}")
        print(f"Page title: {page_title}")

    print(f"\n--- Extracting links from: {page.url} ---")
    print(f"Page title: {page.title()}")

    links_data = extract_links_from_page(page)

    output_data = {
        "page_url": course_page_url,
        "page_title": page.title(),
        "links": links_data,
        "total_links": len(links_data),
        "video_links_count": sum(
            1 for link in links_data if link.get("video_provider")
        ),
    }

    with open(OUTPUT_FILE, "w") as f:
        json.dump(output_data, f, indent=2)

    print(f"\n--- Results ---")
    print(f"Page title: {output_data['page_title']}")
    print(f"Total links found: {output_data['total_links']}")
    print(f"Video links found: {output_data['video_links_count']}")
    print(f"Results saved to: {OUTPUT_FILE}")

    print("\n--- Video Links Detected ---")
    for link in links_data:
        if link.get("video_provider"):
            print(f"  - [{link['video_provider']}] {link['text'][:50]}...")

    input("\nPress Enter to close browser...")
    browser.close()
    playwright.stop()


if __name__ == "__main__":
    main()
