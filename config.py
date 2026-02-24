"""
Central configuration for canvas-transcriber.

Values are read from environment variables or a .env file (via python-dotenv).
Command-line flags always take precedence over these defaults.
"""

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv is optional; fall back to plain env vars

# Paths
SESSION_FILE = Path(os.getenv("CT_SESSION_FILE", "session.json"))
LINKS_FILE = Path(os.getenv("CT_LINKS_FILE", "links_output.json"))
OUTPUT_DIR = Path(os.getenv("CT_OUTPUT_DIR", "transcripts"))

# Behaviour
LOGIN_TIMEOUT = int(os.getenv("CT_LOGIN_TIMEOUT", "180"))
HEADLESS = os.getenv("CT_HEADLESS", "false").lower() in ("1", "true", "yes")
