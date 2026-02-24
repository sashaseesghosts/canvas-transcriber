# Security policy

## What to keep private

| File / directory | Why |
|---|---|
| `session.json` | Contains your Canvas authentication cookies. Anyone with this file can access your account. |
| `links_output.json` | Lists all video URLs from your course — may be considered non-public. |
| `transcripts/` | Your course transcripts. Redistributing these may violate your institution's academic-integrity or copyright policies. |
| `.env` | May contain custom paths or tokens. |

All of the above are excluded from version control by `.gitignore`. **Do not force-add them.**

## Reporting a vulnerability

If you find a security issue in this project (e.g. a path that could expose session files to a third party), please open a GitHub issue with the label **security** or email the maintainer directly. Do not include real session files or credentials in any report.

## Responsible use

This tool is intended for **personal study** on courses you are enrolled in and authorised to access. Do not use it to scrape courses you are not enrolled in, to distribute copyrighted lecture content, or in any way that violates your institution's terms of service.
