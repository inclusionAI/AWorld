---
name: tiktok_hot_video_url_acquire
description: Acquire the URL of the best-performing TikTok video (highest likes) for one keyword by activating agent-browser, connecting via CDP port 9222, reusing existing logged-in TikTok tab when available, running exactly one search, and selecting the top-like result page.
---

# TikTok Hot Video URL Acquire

## Scope

This skill does only one task:
- Find and return one TikTok video URL with the highest like count from a single keyword search result page.

Do not do analytics, downloads, or TikHub steps.

## Required setup

1. Activate browser automation skill:
```bash
SKILL__active_skill(skill_name="agent-browser")
```

2. Connect through CDP on port `9222`.
3. Check existing tabs first:
   - If a logged-in TikTok page already exists in this Chrome session, reuse it directly.
   - If not, open TikTok and proceed.

## Execution rules

- Perform exactly one keyword search.
- Complete selection on that single result page (no repeated searches).
- Primary selection metric: highest likes.
- End task immediately after obtaining and returning the target video URL.

## Standard workflow

### 1) Connect and locate TikTok tab

```bash
agent-browser --cdp 9222 tab
```

- Identify whether there is already a TikTok tab with active login state.
- Switch to that tab if found.

### 2) Run one keyword search

```bash
agent-browser --cdp 9222 snapshot -i
agent-browser --cdp 9222 click @e<search_input_ref>
agent-browser --cdp 9222 fill @e<search_input_ref> "<keyword>"
agent-browser --cdp 9222 press Enter
agent-browser --cdp 9222 wait 3000
```

Notes:
- Prefer stable searchable input (`combobox`/search input).
- Use `fill` (not `type`) to avoid leftover text.

### 3) Collect search result cards and compare likes

Use page extraction (for example via `eval`) on the current result page to:
- Read candidate video cards.
- Parse like counts (`M`, `K`, plain numbers).
- Determine the single best-performing result (highest likes).

If parsing is ambiguous, re-snapshot or re-extract on the same page; do not perform another search.

### 4) Open target video and capture URL

- Click the chosen best-performing video card.
- Wait for detail page load (`wait 2000` or more if needed).
- Get the current URL:

```bash
agent-browser --cdp 9222 get url
```

Expected format:
- `https://www.tiktok.com/@<username>/video/<video_id>`

## Output

Return only:
- Target keyword
- Selected video likes summary (short)
- Final video URL

Then stop.

## Reliability notes

- If element references become stale, run `snapshot -i` again.
- If click fails, use DOM-based click in `eval`.
- If result page is not ready, increase wait time slightly and retry extraction on the same page.
