"""
main.py — deprecated entry point.

Please use cli.py instead:

    python cli.py extract-page  <url>
    python cli.py crawl-course  <url>
    python cli.py extract-video [options]

Run ``python cli.py --help`` for full usage.
"""

import sys

print("main.py is no longer the entry point.")
print("Use:  python cli.py --help")
sys.exit(1)
