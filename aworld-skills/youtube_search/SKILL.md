---
name: tikhub-youtube-search
description: Lightweight TikHub YouTube search and video-detail workflow. Prioritizes single-request usage with curl or minimal Python, saves raw API JSON by default, and includes a small stdlib post-processor for CSV and simplified JSON. Use when the user wants YouTube comprehensive search results, continuation-token pagination, or structured video metadata from TikHub without a heavy wrapper.
---

# TikHub YouTube Search

## What this skill gives you

This skill is optimized for the **common case: one search or one video detail request**.

It provides:

1. **Minimal request patterns**
   - `curl` for quickest validation
   - tiny `httpx` example for people who prefer Python

2. **Raw JSON saving**
   - save the full TikHub response after each request
   - useful for audit, replay, and later post-processing

3. **One optional post-processor**
   - `postprocess_youtube_raw.py`
   - reads one raw file or a directory of raw files
   - writes `youtube_search_summary.csv` and `youtube_search_summary.json`

4. **Optional batch guidance**
   - enough information for concurrent use later
   - intentionally brief, not the main path

Does **not** import `TikHub-Multi-Functional-Downloader` or any other project package.

## API key requirement

This skill intentionally does **not** contain any API key.

Use one of these:

- environment variable: `TIKHUB_API_KEY`
- ask the user to provide an API key explicitly

If the key is missing, stop and ask for it instead of hardcoding one into scripts.

## Install

```bash
pip install httpx
```

Post-processor: **no extra packages**.

## API (for reference)

- Search:
  `GET https://api.tikhub.io/api/v1/youtube/web_v2/get_general_search_v2?keyword=...`
- Video details:
  `GET https://api.tikhub.io/api/v1/youtube/web_v2/get_video_info?video_id=...&language_code=zh-CN&need_format=true`
- Header:
  `Authorization: Bearer <API_KEY>`

## Notes from real requests

- `keyword` should be passed through request params or URL-encoded.
- `need_format` should be sent as `true` or `false`; a blank parameter may fail boolean parsing.
- Search responses usually contain `data.videos`, `data.shorts`, `data.channels`, `data.playlists`, and sometimes `continuation_token`.
- Detail responses may have empty structured fields while display fields are populated; `view_count_text`, `like_count_text`, and `date_text` are often more reliable than `view_count`, `like_count`, and `upload_date`.

---

## Preferred path: single request

### 1. Quickest: `curl`

Search:

```bash
curl --location --request GET "https://api.tikhub.io/api/v1/youtube/web_v2/get_general_search_v2?keyword=Python%20tutorial" \
--header "Authorization: Bearer $TIKHUB_API_KEY"
```

Video detail:

```bash
curl --location --request GET "https://api.tikhub.io/api/v1/youtube/web_v2/get_video_info?video_id=_uQrJ0TkZlc&language_code=zh-CN&need_format=true" \
--header "Authorization: Bearer $TIKHUB_API_KEY"
```

With optional search filters:

```bash
curl --location --request GET "https://api.tikhub.io/api/v1/youtube/web_v2/get_general_search_v2?keyword=cute%20cats&type=video&sort_by=relevance" \
--header "Authorization: Bearer $TIKHUB_API_KEY"
```

### 2. Preferred Python pattern: tiny `httpx`

If the user wants Python, prefer a **small request snippet**, not a framework.

Search and save raw JSON:

```python
import json
import os
import httpx

api_key = os.getenv("TIKHUB_API_KEY", "").strip()
if not api_key:
    raise SystemExit("Missing TIKHUB_API_KEY")

url = "https://api.tikhub.io/api/v1/youtube/web_v2/get_general_search_v2"
params = {"keyword": "Python tutorial"}
headers = {"Authorization": f"Bearer {api_key}", "Accept": "*/*"}

with httpx.Client(timeout=30.0, follow_redirects=True) as client:
    raw = client.get(url, params=params, headers=headers).json()

with open("youtube_search_raw.json", "w", encoding="utf-8") as f:
    json.dump(raw, f, ensure_ascii=False, indent=2)

for item in raw.get("data", {}).get("videos", [])[:5]:
    print(item.get("title", ""))
    print(item.get("url", ""))
```

Detail and save raw JSON:

```python
import json
import os
import httpx

api_key = os.getenv("TIKHUB_API_KEY", "").strip()
if not api_key:
    raise SystemExit("Missing TIKHUB_API_KEY")

url = "https://api.tikhub.io/api/v1/youtube/web_v2/get_video_info"
params = {
    "video_id": "_uQrJ0TkZlc",
    "language_code": "zh-CN",
    "need_format": "true",
}
headers = {"Authorization": f"Bearer {api_key}", "Accept": "*/*"}

with httpx.Client(timeout=30.0, follow_redirects=True) as client:
    raw = client.get(url, params=params, headers=headers).json()

with open("youtube_detail_raw.json", "w", encoding="utf-8") as f:
    json.dump(raw, f, ensure_ascii=False, indent=2)

data = raw.get("data", {})
print("title:", data.get("title", ""))
print("author:", data.get("author", ""))
print("views:", data.get("view_count_text", "") or data.get("view_count", ""))
print("video_url:", data.get("video_url", ""))
```

---

## Save raw JSON by default

For this workflow, the recommended default is:

1. request the API
2. save the **full** raw JSON immediately
3. print only a few useful fields for quick inspection
4. optionally run the post-processor later

Suggested file naming:

- search raw: `search_<keyword>_<request_id>.json`
- detail raw: `detail_<video_id>_<request_id>.json`

If `request_id` is unavailable, hash the keyword or video ID.

---

## Post-process raw JSON

Save as `postprocess_youtube_raw.py` (**stdlib only**).

Input:

- one raw search/detail JSON file
- or a directory containing multiple raw JSON files

Output:

- `youtube_search_summary.csv`
- `youtube_search_summary.json`

```python
#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from glob import glob
from typing import Any, Dict, List


def collect_inputs(path: str) -> List[str]:
    if os.path.isfile(path):
        return [path]
    if os.path.isdir(path):
        return sorted(glob(os.path.join(path, "*.json")))
    raise FileNotFoundError(path)


def as_list(value: Any) -> List[dict]:
    return value if isinstance(value, list) else []


def flatten_for_csv(row: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in row.items():
        if v is None:
            out[k] = ""
        elif isinstance(v, (dict, list)):
            out[k] = json.dumps(v, ensure_ascii=False)
        else:
            out[k] = v
    return out


def simplify_search(raw: dict, source_file: str) -> Dict[str, Any]:
    data = raw.get("data") or {}
    videos = as_list(data.get("videos"))
    first_video = videos[0] if videos else {}
    return {
        "source_file": os.path.basename(source_file),
        "record_type": "search",
        "request_id": raw.get("request_id"),
        "api_code": raw.get("code"),
        "router": raw.get("router"),
        "keyword": data.get("keyword") or (raw.get("params") or {}).get("keyword", ""),
        "video_count": len(videos),
        "short_count": len(as_list(data.get("shorts"))),
        "channel_count": len(as_list(data.get("channels"))),
        "playlist_count": len(as_list(data.get("playlists"))),
        "continuation_token": data.get("continuation_token", ""),
        "top_video_id": first_video.get("video_id", ""),
        "top_video_title": first_video.get("title", ""),
        "top_video_author": first_video.get("author", ""),
        "top_video_url": first_video.get("url", ""),
        "top_video_views": first_video.get("view_count", ""),
    }


def simplify_detail(raw: dict, source_file: str) -> Dict[str, Any]:
    data = raw.get("data") or {}
    return {
        "source_file": os.path.basename(source_file),
        "record_type": "detail",
        "request_id": raw.get("request_id"),
        "api_code": raw.get("code"),
        "router": raw.get("router"),
        "video_id": data.get("video_id", ""),
        "title": data.get("title", ""),
        "author": data.get("author", ""),
        "channel_id": data.get("channel_id", ""),
        "channel_handle": data.get("channel_handle", ""),
        "channel_url": data.get("channel_url", ""),
        "is_verified": data.get("is_verified"),
        "view_count": data.get("view_count", ""),
        "view_count_text": data.get("view_count_text", ""),
        "like_count": data.get("like_count", ""),
        "like_count_text": data.get("like_count_text", ""),
        "comment_count": data.get("comment_count", ""),
        "date_text": data.get("date_text", ""),
        "relative_date_text": data.get("relative_date_text", ""),
        "length_seconds": data.get("length_seconds", ""),
        "video_url": data.get("video_url", ""),
        "playability_status": data.get("playability_status", ""),
        "chapter_count": len(as_list(data.get("chapters"))),
    }


def simplify_raw(raw: dict, source_file: str) -> Dict[str, Any]:
    router = raw.get("router") or ""
    if "get_general_search_v2" in router:
        return simplify_search(raw, source_file)
    if "get_video_info" in router:
        return simplify_detail(raw, source_file)
    return {
        "source_file": os.path.basename(source_file),
        "record_type": "unknown",
        "request_id": raw.get("request_id"),
        "api_code": raw.get("code"),
        "router": router,
        "error": "unsupported router for this post-processor",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Raw TikHub YouTube JSON -> CSV + simplified JSON")
    ap.add_argument("--input", "-i", required=True, help="One .json file or a directory of .json")
    ap.add_argument("--out-dir", "-o", default=".", help="Output directory (default: current working directory)")
    args = ap.parse_args()

    try:
        files = collect_inputs(args.input)
    except FileNotFoundError as e:
        print("Input not found:", e, file=sys.stderr)
        return 2

    if not files:
        print("No JSON files found.", file=sys.stderr)
        return 2

    out_dir = os.path.abspath(args.out_dir)
    os.makedirs(out_dir, exist_ok=True)
    csv_path = os.path.join(out_dir, "youtube_search_summary.csv")
    json_path = os.path.join(out_dir, "youtube_search_summary.json")

    rows: List[Dict[str, Any]] = []
    for fp in files:
        try:
            with open(fp, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception as ex:
            rows.append({"source_file": os.path.basename(fp), "record_type": "broken_json", "error": f"json load: {ex}"})
            continue
        rows.append(simplify_raw(raw, fp))

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "generated_from": os.path.abspath(args.input),
                "record_count": len(rows),
                "records": rows,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    flat = [flatten_for_csv(r) for r in rows]
    fieldnames = sorted({k for row in flat for k in row.keys()})
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in flat:
            writer.writerow({k: row.get(k, "") for k in fieldnames})

    print("Wrote:", csv_path)
    print("Wrote:", json_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Commands:

```bash
python postprocess_youtube_raw.py --input ./youtube_api_raw
python postprocess_youtube_raw.py --input ./youtube_detail_raw.json --out-dir .
```

---

## Optional: concurrent or multi-request usage

Only use this when the user clearly needs **many keywords**, **many video IDs**, or pagination at scale.

Keep the batching layer thin:

1. accept a text file of keywords or video IDs
2. call the same two endpoints in a loop or thread pool
3. save one raw JSON per request
4. reuse `postprocess_youtube_raw.py` afterward

Recommended limits:

- start with `max_workers=3` to `5`
- reduce concurrency if you hit `429`
- keep filenames stable and collision-safe

Do **not** lead with a big wrapper if the task is only one search or one detail lookup.

---

## End-to-end workflow

1. Provide `TIKHUB_API_KEY`.
2. Make a single search or detail request with `curl` or a tiny `httpx` snippet.
3. Save the full raw response JSON.
4. Inspect a few important fields directly.
5. If needed, run `postprocess_youtube_raw.py` on one file or a directory of raw files.

## Troubleshooting

- **`401/403`**: invalid API key or missing YouTube scopes.
- **`429`**: rate limit; retry later or reduce concurrency.
- **`need_format` error**: pass `true` or `false`, not a blank query parameter.
- **Empty structured fields in detail**: prefer `view_count_text`, `like_count_text`, and `date_text`.
- **No search results**: keyword too narrow, region-dependent results, or temporary upstream changes.

## What this skill does *not* cover

- downloading YouTube media streams
- comment crawling
- transcript extraction
- channel-wide crawling beyond what the search response already includes

---
