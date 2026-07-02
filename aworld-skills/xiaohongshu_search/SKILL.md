---
name: tikhub-xiaohongshu-search
description: Lightweight TikHub Xiaohongshu image-search workflow. Prioritizes single-request usage with curl or minimal Python, saves raw API JSON by default, and includes a small stdlib post-processor for CSV and simplified JSON. Use when the user wants Xiaohongshu keyword image search, page-based pagination, or structured note/image metadata from TikHub without a heavy wrapper.
---

# TikHub Xiaohongshu Search

## What this skill gives you

This skill is optimized for the **common case: one keyword search request**.

It provides:

1. **Minimal request patterns**
   - `curl` for quickest validation
   - tiny `httpx` example for people who prefer Python

2. **Raw JSON saving**
   - save the full TikHub response after each request
   - useful for audit, replay, and later post-processing

3. **One optional post-processor**
   - `postprocess_xiaohongshu_raw.py`
   - reads one raw file or a directory of raw files
   - writes `xiaohongshu_search_summary.csv` and `xiaohongshu_search_summary.json`

4. **Optional pagination guidance**
   - enough information for later page turning
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

- Image search:
  `GET https://api.tikhub.io/api/v1/xiaohongshu/app_v2/search_images?keyword=...&page=1&source=explore_feed`
- Header:
  `Authorization: Bearer <API_KEY>`

## Notes from real requests

- In `curl`, Chinese keywords should be URL-encoded. Directly putting `壁纸` into the query caused `400`, while `%E5%A3%81%E7%BA%B8` succeeded.
- A working minimal first-page request was:
  `keyword=%E5%A3%81%E7%BA%B8&page=1&source=explore_feed`
- The first-page response returns pagination context:
  `search_id`, `search_session_id`, `word_request_id`, and `next_page`
- Search results are in:
  `data.data.items`
- Useful nested sections include:
  `image_info`, `note_info`, `share_info`, and `user_info`

---

## Preferred path: single request

### 1. Quickest: `curl`

First page:

```bash
curl --location --request GET "https://api.tikhub.io/api/v1/xiaohongshu/app_v2/search_images?keyword=%E5%A3%81%E7%BA%B8&page=1&source=explore_feed" \
--header "Authorization: Bearer $TIKHUB_API_KEY"
```

Another keyword example:

```bash
curl --location --request GET "https://api.tikhub.io/api/v1/xiaohongshu/app_v2/search_images?keyword=%E6%B2%BB%E6%84%88%E7%B3%BB&page=1&source=explore_feed" \
--header "Authorization: Bearer $TIKHUB_API_KEY"
```

### 2. Preferred Python pattern: tiny `httpx`

If the user wants Python, prefer a **small request snippet**, not a framework.

Search and save raw JSON:

```python
import json
import os
import urllib.parse
import httpx

api_key = os.getenv("TIKHUB_API_KEY", "").strip()
if not api_key:
    raise SystemExit("Missing TIKHUB_API_KEY")

keyword = "壁纸"
url = "https://api.tikhub.io/api/v1/xiaohongshu/app_v2/search_images"
params = {
    "keyword": keyword,
    "page": 1,
    "source": "explore_feed",
}
headers = {"Authorization": f"Bearer {api_key}", "Accept": "*/*"}

with httpx.Client(timeout=30.0, follow_redirects=True) as client:
    raw = client.get(url, params=params, headers=headers).json()

safe_keyword = urllib.parse.quote(keyword, safe="")
with open(f"xiaohongshu_search_{safe_keyword}.json", "w", encoding="utf-8") as f:
    json.dump(raw, f, ensure_ascii=False, indent=2)

items = raw.get("data", {}).get("data", {}).get("items", [])
for item in items[:5]:
    note = item.get("note_info", {})
    share = item.get("share_info", {})
    user = item.get("user_info", {})
    print(note.get("title", ""))
    print(share.get("link", ""))
    print(user.get("nickname", ""))
```

---

## Save raw JSON by default

For this workflow, the recommended default is:

1. request the API
2. save the **full** raw JSON immediately
3. print only a few useful fields for quick inspection
4. optionally run the post-processor later

Suggested file naming:

- first page raw: `search_<keyword>_page1_<request_id>.json`
- next page raw: `search_<keyword>_page2_<request_id>.json`

If `request_id` is unavailable, hash the keyword plus page number.

---

## Pagination

Only care about this if the user wants page 2 or beyond.

From the first response, keep these fields:

- `search_id`
- `search_session_id`
- `word_request_id`
- `next_page`

Then use them in the next request:

```bash
curl --location --request GET "https://api.tikhub.io/api/v1/xiaohongshu/app_v2/search_images?keyword=%E5%A3%81%E7%BA%B8&page=2&search_id=<search_id>&search_session_id=<search_session_id>&word_request_id=<word_request_id>&source=explore_feed" \
--header "Authorization: Bearer $TIKHUB_API_KEY"
```

If the endpoint behavior changes, trust the latest response fields over assumptions.

---

## Post-process raw JSON

Save as `postprocess_xiaohongshu_raw.py` (**stdlib only**).

Input:

- one raw search JSON file
- or a directory containing multiple raw JSON files

Output:

- `xiaohongshu_search_summary.csv`
- `xiaohongshu_search_summary.json`

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


def simplify_raw(raw: dict, source_file: str) -> Dict[str, Any]:
    outer = raw.get("data") or {}
    inner = outer.get("data") or {}
    items = as_list(inner.get("items"))
    first = items[0] if items else {}
    note = first.get("note_info") or {}
    share = first.get("share_info") or {}
    user = first.get("user_info") or {}
    image = first.get("image_info") or {}
    return {
        "source_file": os.path.basename(source_file),
        "request_id": raw.get("request_id"),
        "api_code": raw.get("code"),
        "router": raw.get("router"),
        "keyword": (raw.get("params") or {}).get("keyword", ""),
        "page": inner.get("page"),
        "next_page": inner.get("next_page"),
        "search_id": inner.get("search_id", ""),
        "search_session_id": inner.get("search_session_id", ""),
        "word_request_id": inner.get("word_request_id", ""),
        "item_count": len(items),
        "top_note_id": note.get("note_id", ""),
        "top_title": note.get("title", ""),
        "top_desc": note.get("desc", ""),
        "top_liked_count": note.get("liked_count"),
        "top_collected_count": note.get("collected_count"),
        "top_comments_count": note.get("comments_count"),
        "top_share_link": share.get("link", ""),
        "top_user_nickname": user.get("nickname", ""),
        "top_user_id": user.get("user_id", ""),
        "top_image_url": image.get("url", ""),
        "top_image_original": image.get("original", ""),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Raw TikHub Xiaohongshu JSON -> CSV + simplified JSON")
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
    csv_path = os.path.join(out_dir, "xiaohongshu_search_summary.csv")
    json_path = os.path.join(out_dir, "xiaohongshu_search_summary.json")

    rows: List[Dict[str, Any]] = []
    for fp in files:
        try:
            with open(fp, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception as ex:
            rows.append({"source_file": os.path.basename(fp), "error": f"json load: {ex}"})
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
python postprocess_xiaohongshu_raw.py --input ./xiaohongshu_raw
python postprocess_xiaohongshu_raw.py --input ./search_%E5%A3%81%E7%BA%B8_page1.json --out-dir .
```

---

## Optional: multiple pages or multiple keywords

Only use this when the user clearly needs:

- multiple keywords
- page 2+
- bulk result collection

Keep the batching layer thin:

1. accept a list of keywords
2. request page 1 first
3. store the returned pagination fields
4. fetch more pages only if needed
5. save one raw JSON per request
6. reuse `postprocess_xiaohongshu_raw.py` afterward

Recommended limits:

- start sequentially or with `max_workers=2` to `3`
- reduce concurrency if you hit `429`
- avoid assuming pagination tokens are reusable across different keywords

Do **not** lead with a big wrapper if the task is only one keyword search.

---

## End-to-end workflow

1. Provide `TIKHUB_API_KEY`.
2. Make a single image-search request with `curl` or a tiny `httpx` snippet.
3. Save the full raw response JSON.
4. Inspect a few important fields directly.
5. If needed, run `postprocess_xiaohongshu_raw.py` on one file or a directory of raw files.
6. Only then expand to page 2+ or multiple keywords.

## Troubleshooting

- **`401/403`**: invalid API key or missing Xiaohongshu scopes.
- **`400` with Chinese keyword in curl**: URL-encode the keyword.
- **No items**: keyword too narrow, source changed, or upstream result shape changed.
- **`429`**: rate limit; retry later or reduce concurrency.
- **Page 2 fails**: confirm you passed the latest `search_id`, `search_session_id`, and `word_request_id` from the prior response.

## What this skill does *not* cover

- note detail endpoints
- note comment crawling
- downloading all images from every note as a batch export
- non-search Xiaohongshu workflows

---
