import json
from pathlib import Path


def load_session(context, session_file: Path) -> bool:
    """Load session cookies from file. Returns True if successful."""
    try:
        with open(session_file, "r") as f:
            cookies = json.load(f)
        context.add_cookies(cookies)
        return True
    except Exception as e:
        print(f"Failed to load session: {e}")
        return False
