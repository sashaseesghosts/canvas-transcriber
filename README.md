# Canvas Lectures Transcriber - MVP

A Python project that uses Playwright to log into Canvas, save the authenticated session, and extract video links from a course page.

## Features

- Login to Canvas in a real browser (non-headless)
- Manual SSO/MFA support - you complete authentication yourself
- Session persistence - cookies saved for reuse
- Extract all links from a Canvas course page
- Detect video providers: Panopto, Kaltura, Yuja, Zoom, YouTube, Canvas Media, Vimeo
- Output results to JSON file

## Setup

1. **Install Python dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Install Playwright browsers:**
   ```bash
   playwright install chromium
   ```

## Usage

Run the script with a Canvas course page URL:

```bash
python main.py <canvas_course_page_url>
```

### Example

```bash
python main.py https://<school>.instructure.com/courses/123456/pages/module-1
```

## Workflow

1. **First Run:**
   - A browser window opens at the Canvas login page
   - Complete your SSO/MFA authentication manually
   - Press Enter when logged in
   - Session is saved to `session.json`

2. **Subsequent Runs:**
   - Script asks if you want to load existing session
   - If yes, skips login and uses saved cookies
   - If session expired, you'll need to log in again

3. **Link Extraction:**
   - Navigates to the provided course page URL
   - Extracts all links and detects video providers
   - Saves results to `links_output.json`

## Output

The script generates `links_output.json` with the following structure:

```json
{
  "page_url": "https://<school>.instructure.com/courses/123456/pages/module-1",
  "page_title": "Module 1 - Course Title",
  "links": [
    {
      "text": "Lecture 1 Video",
      "href": "https://<school>.hosted.panopto.com/...",
      "video_provider": "panopto"
    },
    {
      "text": "Canvas Media",
      "href": "https://<school>.instructure.com/media/...",
      "video_provider": "canvas_media"
    }
  ],
  "total_links": 42,
  "video_links_count": 5
}
```

## Files

- `main.py` - Main entry point
- `login.py` - Session management
- `extractor.py` - Link extraction and video detection
- `session.json` - Saved authentication cookies (created on first run)
- `links_output.json` - Extracted links (created after extraction)

## Notes

- Currently configured for UT Austin's Canvas (`<school>.instructure.com`). Modify `CANVAS_URL` in `main.py` for other institutions.
- Only scrapes a single page URL provided as argument, not the entire course.
- Video provider detection is based on URL patterns.

## Kaltura Transcript Extraction

After running `main.py` to extract links, you can extract transcripts from Kaltura videos:

### Step 1: Extract links from Canvas page

```bash
python main.py <canvas_course_page_url>
```

This creates `links_output.json` with detected video links.

### Step 2: Extract transcripts from Kaltura videos

```bash
python transcript_kaltura.py
```

This will:
1. Read `links_output.json` and filter for Kaltura links
2. Open each Kaltura video in the browser (non-headless)
3. Attempt to extract transcript/captions from the player UI
4. Save transcripts to `transcripts/<video_title>.txt`
5. Save metadata to `transcripts/metadata.json`

### Transcript Extraction Methods

The script tries multiple methods to find transcripts:
- Click transcript/captions button in the player
- Look for transcript panel/tab in the page
- Find VTT/SRT caption track references
- Look for download transcript links
- Check player API data
- Monitor network requests for caption files

### Output Files

- `transcripts/<safe_title>.txt` - Transcript text (if found)
- `transcripts/metadata.json` - Metadata for each video:

```json
{
  "total_videos": 2,
  "transcripts_found": 1,
  "videos": [
    {
      "title": "Module 1 Introduction",
      "source_url": "https://...",
      "provider": "kaltura",
      "link_type": "iframe",
      "transcript_found": true,
      "transcript_source_type": "ui_panel",
      "transcript_text": "...",
      "errors": []
    }
  ]
}
```
