# canvas-transcriber

Extract transcripts from Kaltura lecture videos embedded in Canvas LMS — for personal study on courses you are authorised to access.

## What it does

1. **Crawls** a Canvas course modules page and finds every Kaltura video link.
2. **Intercepts** the Kaltura player's internal caption API request during page load to get a signed URL pointing to the actual SRT/VTT file.
3. **Saves** each transcript as a plain `.txt` file, organised by module.
4. **Generates** a compact `metadata.json` index (no full transcript text — just paths and previews).

YouTube and Vimeo links are detected and recorded in the links file but transcript extraction is not supported; only Kaltura is in scope.

## Who it is for

Students who want to use their own lecture transcripts for study (notes, summaries, flashcards). You must be **enrolled in the course** and authorised to watch the videos. Do not share transcripts publicly or redistribute course content.

## How it works

```
Canvas /modules page
        │
        ▼  (cli.py crawl-course)
  Visit each module item
        │
        ▼  (extractor.py)
  Collect Kaltura iframe embed URLs
        │
        ▼  (cli.py extract-video)
  Open each video page in Playwright
        │
        ├─► Intercept caption_captionasset/getUrl API response
        │         └─► Fetch signed SRT/VTT serve URL → parse → transcript ✓
        │
        └─► Fallback: scrape transcript panel from player DOM
```

The Kaltura player fires a `caption_captionasset/getUrl` request during initialisation. The tool intercepts that response, extracts the signed `cfvod.kaltura.com/.../serve/...` URL, and fetches the caption file directly — no UI interaction required.

**Requirement:** the video must have captions/subtitles enabled in Kaltura. Videos without captions will be recorded as failed in `metadata.json`.

## Installation

```bash
# 1. Clone
git clone https://github.com/<you>/canvas-transcriber
cd canvas-transcriber

# 2. Create and activate a virtual environment (recommended)
python -m venv .venv
source .venv/bin/activate        # macOS / Linux
# .venv\Scripts\activate         # Windows

# 3. Install Python dependencies
pip install -r requirements.txt

# 4. Install the Playwright Chromium browser
playwright install chromium
```

Optional: copy `.env.example` to `.env` and set any overrides.

## Usage

### Step 1 — Crawl your Canvas course

```bash
python cli.py crawl-course "https://<school>.instructure.com/courses/<course-id>/modules"
```

This opens a browser window, waits for you to complete SSO/MFA login, then crawls all module items and saves `links_output.json`.

On the first run you will be prompted to log in manually. After that the session is saved to `session.json` and reused automatically.

### Step 2 — Extract transcripts

```bash
python cli.py extract-video
```

Processes every Kaltura link in `links_output.json`, saves transcripts as `.txt` files under `transcripts/<Module_Name>/`, and writes `transcripts/metadata.json`.

### Retry failures

If some videos failed (no captions or a transient network error), retry just those:

```bash
python cli.py extract-video --retry-failed
```

### Single page (not a full course)

```bash
python cli.py extract-page "https://<school>.instructure.com/courses/<course-id>/pages/<page-slug>"
```

### All options

```
python cli.py --help
python cli.py crawl-course --help
python cli.py extract-video --help
```

Key flags:

| Flag | Default | Description |
|---|---|---|
| `--session-file` | `session.json` | Cookie file path |
| `--output` | `links_output.json` | Link extraction output |
| `--output-dir` | `transcripts` | Transcript directory |
| `--headless` | `false` | Run browser without a window |
| `--login-timeout` | `180` | Seconds to wait for SSO/MFA |
| `--debug` | `false` | Deep-inspect first video, save `kaltura_debug.json` |
| `--retry-failed` | `false` | Retry videos that failed previously |

Environment variables (or `.env` file): `CT_SESSION_FILE`, `CT_LINKS_FILE`, `CT_OUTPUT_DIR`, `CT_LOGIN_TIMEOUT`, `CT_HEADLESS`.

## SSO / MFA login

The browser opens **non-headless** so you can complete your institution's SSO or MFA flow manually. After you land on a Canvas course page, the script detects this automatically and continues.

Your session cookies are saved locally to `session.json` and reused on subsequent runs. **Never commit `session.json` to version control.**

## Output files

```
links_output.json               — all video links found (not committed)
transcripts/
  metadata.json                 — per-video status, path, preview (not committed)
  Module_1_Name/
    Video Title.txt             — plain transcript text
    Another Video.txt
  Module_2_Name/
    ...
```

See `examples/sample_links_output.json` and `examples/sample_metadata.json` for the format.

## Limitations

- **Captions must exist.** Videos without Kaltura captions configured will fail with `No caption_captionasset/getUrl intercepted`.
- **Kaltura only.** YouTube/Vimeo/Panopto transcripts are not extracted.
- **Canvas LMS only.** The crawler targets Canvas (`instructure.com`) module pages.
- **Session expiry.** Saved sessions typically last a few days. Re-run and log in again when they expire.
- **Rate limiting.** The tool processes one video at a time with a short delay between requests. Do not modify it to run in parallel on a shared institution server.

## Troubleshooting

**"No Kaltura video links found"**
The modules page may not have loaded fully. Try again; if the problem persists, use `extract-page` on a specific module page instead of `crawl-course`.

**"No caption_captionasset/getUrl intercepted"**
The video has no captions in Kaltura, or the caption plugin did not load. Use `--debug` to inspect the page and check `suggested_actions` in `kaltura_debug.json`.

**Login loop / session expired**
Delete `session.json` and run again to force a fresh login.

**Browser closes immediately**
Run without `--headless` (the default) to see error messages in the browser.

## Debug mode

```bash
python cli.py extract-video --debug
```

Opens the first video, captures all network responses, and saves `transcripts/kaltura_debug.json` with:
- Final page URL and title
- All frames (URL, title, transcript/caption elements found)
- Network responses matching `vtt`, `srt`, `caption`, `transcript`, `kaltura`
- Transcript/CC buttons visible in the page
- Kaltura player config (entryId, captionUrls)
- Suggested next actions

## Privacy and security

- `session.json` contains authentication cookies. Keep it private.
- Transcripts are saved locally only. Nothing is uploaded anywhere.
- See [SECURITY.md](SECURITY.md) for more details.

## Legal / ethical note

Use this tool only on courses you are enrolled in and authorised to access. Review your institution's terms of service and your course's academic-integrity policy before extracting and using transcript content. The authors are not responsible for any misuse.

## License

[MIT](LICENSE)
