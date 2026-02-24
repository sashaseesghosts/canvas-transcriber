# Contributing

Thanks for your interest! This is a small personal-study tool. Contributions that keep it simple, safe, and broadly usable are very welcome.

## Local setup

```bash
git clone https://github.com/<your-fork>/canvas-transcriber
cd canvas-transcriber
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

## Testing safely

There is no automated test suite yet. To verify a change manually:

1. **Import check** — all modules should import without errors:
   ```bash
   python -c "import config, login, extractor, transcript_kaltura, cli; print('OK')"
   ```

2. **CLI help** — all subcommands should print help:
   ```bash
   python cli.py --help
   python cli.py extract-page --help
   python cli.py crawl-course --help
   python cli.py extract-video --help
   ```

3. **Live test** — run against a real Canvas course you are enrolled in.
   Use `--debug` on `extract-video` to inspect a single video without
   extracting all transcripts:
   ```bash
   python cli.py crawl-course "https://<school>.instructure.com/courses/<id>/modules"
   python cli.py extract-video --debug
   ```

## What to keep in mind

- **Never hardcode school-specific URLs or IDs.** Use placeholders like
  `https://<school>.instructure.com/...` in docs and examples.
- **Never commit** `session.json`, `links_output.json`, `transcripts/`, or
  any real course data. They are in `.gitignore` for a reason.
- Keep the browser **non-headless by default** — SSO/MFA requires a visible
  window.
- Prefer incremental changes over large rewrites.

## Pull request checklist

- [ ] `python -c "import config, login, extractor, transcript_kaltura, cli"` passes
- [ ] `python cli.py --help` and all subcommand helps print without error
- [ ] No private URLs, credentials, or course data in the diff
- [ ] `session.json`, `links_output.json`, and `transcripts/` are not staged
