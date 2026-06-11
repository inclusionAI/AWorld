---
name: tiktok_download
description: Single-file TikTok/Douyin video download and traffic metrics via TikHub API using only httpx; optional persisted raw API JSON plus a stdlib post-processor emitting CSV and simplified JSON. Supports one URL or concurrent batch (max 10 workers). No dependency on any project codebase.
---

# TikHub Download Independent

## What this skill gives you

**Two small artifacts** (all copy-pasteable from this file):

1. **`tikhub_independent.py`** (single file, **`httpx` only**)

   - **Single video** or **batch** (parallel, max **10** workers): download MP4 + print metrics
   - **`Raw API JSON`**: **by default** every successful API response is written to disk as a **full** JSON file (same shape as e.g. `raw_api_response.json` in this repo — top-level `code`, `request_id`, `params`, `data.aweme_detail`, etc.). Use `--no-save-raw` to skip.
2. **`postprocess_tikhub_raw.py`** (**stdlib only**: `json`, `csv`, `argparse`, `glob`)

   - Reads one raw file **or** a directory of raw JSON files
   - Writes into the **current working directory** (or `--out-dir`):  
     - **`tikhub_videos_summary.csv`**  
     - **`tikhub_videos_summary.json`** (list of simplified records; one object per video)

**Structured-data reference** (field meanings, nesting): see project doc  
`TikHub_API_数据格式说明.md` (same repo, path from project root). Example raw payload: `raw_api_response.json`.

Does **not** import `TikHub-Multi-Functional-Downloader` or any other project package.

## Install

```bash
pip install httpx
```

Post-processor: **no extra packages**.

## API (for reference)

- TikTok: `GET https://api.tikhub.io/api/v1/tiktok/app/v3/fetch_one_video_by_share_url?share_url=...`
- Douyin: `GET https://api.tikhub.io/api/v1/douyin/app/v3/fetch_one_video_by_share_url?share_url=...`
- Header: `Authorization: Bearer <API_KEY>`

## Mandatory prerequisite: valid TikHub API key

This skill may be used only when a **valid TikHub API key** is available.

Agent rule:

1. First check whether this skill file already contains a usable API key value in the `API_KEY` default section.
2. If the key is missing, empty, placeholder-only, or otherwise unavailable, **stop the current task immediately**.
3. The agent must then tell the human user:
   - they need to go to [https://tikhub.io/](https://tikhub.io/) and apply for their own **paid personal API key**
   - then fill that key into this file: `/AWorld/aworld-skills/tikhub_download/SKILL.md`
4. Until the human has provided and filled in a valid key, do **not** continue with download, data fetching, retry, workaround, or fabricated-output behavior.

Concrete example:

- If this file no longer contains a real key in the relevant `API_KEY` location, treat the key as unavailable and follow the stop-and-notify rule above.

---

## Part A — `tikhub_independent.py` (download + optional raw JSON)

Save the following as `tikhub_independent.py`.

**Behavior note:** After each `fetch_video_info` call, if saving is enabled (default), the **entire** parsed JSON object is written with `json.dump(..., indent=2, ensure_ascii=False)` — this is the **audit / replay** artifact for downstream tooling, not the simplified extract.

```python
#!/usr/bin/env python3
"""
TikTok/Douyin: download MP4 + metrics via TikHub API. Optional: save full raw API JSON per request.

Requires: pip install httpx

Usage:
  python tikhub_independent.py one "https://www.tiktok.com/@user/video/123"
  python tikhub_independent.py one "URL" --no-save-raw
  python tikhub_independent.py batch urls.txt
  python tikhub_independent.py batch urls.txt --raw-dir my_raw_dir --max-workers 4

Raw JSON default directory (relative to current working directory): ./tikhub_api_raw
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import sys
from typing import Any, Dict, List
from urllib.parse import urlparse

import httpx

API_KEY = os.getenv(
    "TIKHUB_API_KEY",
    "",
).strip()

MAX_WORKERS_CAP = 10
DEFAULT_OUT = os.path.expanduser("~/Downloads/tikhub_independent")
DEFAULT_RAW_DIR = "tikhub_api_raw"


def clean_name(name: str, max_len: int = 60) -> str:
    name = re.sub(r'[\\/:*?"<>|]+', "_", (name or "").strip())
    name = re.sub(r"\s+", " ", name).strip()
    return (name[:max_len] or "video").strip(" ._")


def platform_from_url(url: str) -> str:
    host = (urlparse(url).netloc or "").lower()
    if "douyin.com" in host:
        return "douyin"
    return "tiktok"


def fetch_video_info(api_key: str, share_url: str) -> dict:
    platform = platform_from_url(share_url)
    endpoint = f"https://api.tikhub.io/api/v1/{platform}/app/v3/fetch_one_video_by_share_url"
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "*/*"}
    params = {"share_url": share_url}
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        resp = client.get(endpoint, headers=headers, params=params)
        resp.raise_for_status()
        return resp.json()


def safe_raw_filename(raw: dict, share_url: str) -> str:
    data = raw.get("data") or {}
    detail = data.get("aweme_detail")
    if not detail and data.get("aweme_details"):
        detail = (data.get("aweme_details") or [None])[0]
    aid = (detail or {}).get("aweme_id") or "unknown"
    rid = (raw.get("request_id") or "noreq").replace("-", "")
    rid = rid[:16] if len(rid) > 16 else rid
    if aid == "unknown":
        h = hashlib.sha256(share_url.encode("utf-8")).hexdigest()[:10]
        return f"raw_unknown_{h}_{rid}.json"
    return f"raw_{aid}_{rid}.json"


def save_raw_json(raw: dict, share_url: str, raw_dir: str) -> str:
    os.makedirs(raw_dir, exist_ok=True)
    path = os.path.join(raw_dir, safe_raw_filename(raw, share_url))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)
    return path


def extract_clean_data(raw: dict) -> dict:
    data = raw.get("data", {})
    detail = data.get("aweme_detail")
    if not detail and data.get("aweme_details"):
        detail = data["aweme_details"][0]
    if not detail:
        return {}

    video = detail.get("video", {})
    play = video.get("play_addr", {}) or {}
    url_list = play.get("url_list") or []
    video_url = url_list[0] if url_list else ""

    author = detail.get("author", {}) or {}
    stats = detail.get("statistics", {}) or {}

    return {
        "id": detail.get("aweme_id", ""),
        "desc": detail.get("desc", ""),
        "author_name": author.get("nickname", ""),
        "create_time": detail.get("create_time", 0),
        "video_url": video_url,
        "like_count": stats.get("digg_count", 0),
        "comment_count": stats.get("comment_count", 0),
        "share_count": stats.get("share_count", 0),
        "play_count": stats.get("play_count", 0),
        "duration": video.get("duration", 0),
        "width": play.get("width", 0),
        "height": play.get("height", 0),
    }


def download_file(url: str, output_path: str) -> None:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
    }
    with httpx.Client(timeout=httpx.Timeout(60.0, read=300.0), follow_redirects=True) as client:
        with client.stream("GET", url, headers=headers) as r:
            r.raise_for_status()
            with open(output_path, "wb") as f:
                for chunk in r.iter_bytes(chunk_size=8192):
                    if chunk:
                        f.write(chunk)


def metrics_dict(info: dict) -> Dict[str, Any]:
    return {
        "video_id": info["id"],
        "author": info.get("author_name", ""),
        "likes": int(info.get("like_count") or 0),
        "comments": int(info.get("comment_count") or 0),
        "shares": int(info.get("share_count") or 0),
        "plays": int(info.get("play_count") or 0),
        "duration_ms": int(info.get("duration") or 0),
        "resolution": f"{int(info.get('width') or 0)}x{int(info.get('height') or 0)}",
    }


def run_one(
    share_url: str,
    out_dir: str,
    raw_dir: str,
    save_raw: bool,
) -> int:
    if not API_KEY:
        print(
            "Error: no valid TikHub API key is available. Stop this task and ask the human user "
            "to apply for a paid personal API key at https://tikhub.io/ and fill it into "
            "/AWorld/aworld-skills/tikhub_download/SKILL.md.",
            file=sys.stderr,
        )
        return 3
    os.makedirs(out_dir, exist_ok=True)

    raw = fetch_video_info(API_KEY, share_url)
    raw_path = None
    if save_raw:
        raw_path = save_raw_json(raw, share_url, raw_dir)
        print("Raw API JSON:", raw_path)

    info = extract_clean_data(raw)
    if not info or not info.get("id") or not info.get("video_url"):
        print("Error: parse failed or video_url missing.", file=sys.stderr)
        print("Raw code/message:", raw.get("code"), raw.get("message"), file=sys.stderr)
        return 4

    base = f"{platform_from_url(share_url)}_{clean_name(info.get('author_name'))}_{info['id']}"
    output_path = os.path.join(out_dir, f"{base}.mp4")
    download_file(info["video_url"], output_path)

    print("Download OK")
    print("Saved:", output_path)
    print("\n=== Metrics ===")
    m = metrics_dict(info)
    for k, v in m.items():
        print(f"{k}: {v}")
    return 0


def process_one_job(
    share_url: str,
    out_dir: str,
    raw_dir: str,
    save_raw: bool,
) -> Dict[str, Any]:
    try:
        raw = fetch_video_info(API_KEY, share_url)
        raw_path = None
        if save_raw:
            raw_path = save_raw_json(raw, share_url, raw_dir)

        info = extract_clean_data(raw)
        if not info or not info.get("id") or not info.get("video_url"):
            return {
                "ok": False,
                "url": share_url,
                "raw_json_path": raw_path,
                "error": f"parse failed code={raw.get('code')} msg={raw.get('message')}",
            }

        base = f"{platform_from_url(share_url)}_{clean_name(info.get('author_name'))}_{info['id']}"
        output_path = os.path.join(out_dir, f"{base}.mp4")
        download_file(info["video_url"], output_path)
        return {
            "ok": True,
            "url": share_url,
            "path": output_path,
            "raw_json_path": raw_path,
            "metrics": metrics_dict(info),
        }
    except Exception as e:
        return {"ok": False, "url": share_url, "error": str(e), "raw_json_path": None}


def read_urls(path: str) -> List[str]:
    urls: List[str] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            u = line.strip()
            if u and not u.startswith("#"):
                urls.append(u)
    return urls


def run_batch(
    urls_file: str,
    out_dir: str,
    max_workers: int,
    raw_dir: str,
    save_raw: bool,
) -> int:
    if not API_KEY:
        print(
            "Error: no valid TikHub API key is available. Stop this task and ask the human user "
            "to apply for a paid personal API key at https://tikhub.io/ and fill it into "
            "/AWorld/aworld-skills/tikhub_download/SKILL.md.",
            file=sys.stderr,
        )
        return 3

    urls = read_urls(urls_file)
    if not urls:
        print("Error: no URLs in file.", file=sys.stderr)
        return 2

    os.makedirs(out_dir, exist_ok=True)
    workers = max(1, min(MAX_WORKERS_CAP, max_workers, len(urls)))

    results: List[Dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [
            ex.submit(process_one_job, u, out_dir, raw_dir, save_raw) for u in urls
        ]
        for fut in concurrent.futures.as_completed(futs):
            results.append(fut.result())

    ok = sum(1 for r in results if r.get("ok"))
    fail = len(results) - ok

    print(f"\n=== Batch summary (workers={workers}, cap={MAX_WORKERS_CAP}) ===")
    print(f"success={ok}, failed={fail}, total={len(urls)}")
    if save_raw:
        print(f"Raw API JSON directory: {raw_dir}")

    for r in sorted(results, key=lambda x: x.get("url", "")):
        if r.get("ok"):
            print(f"\nOK {r['url']}")
            print(f"  video: {r['path']}")
            if r.get("raw_json_path"):
                print(f"  raw:   {r['raw_json_path']}")
            print(f"  metrics: {r['metrics']}")
        else:
            print(f"\nFAIL {r['url']}")
            if r.get("raw_json_path"):
                print(f"  raw (if any): {r['raw_json_path']}")
            print(f"  error: {r.get('error', 'unknown')}")

    return 0 if fail == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="TikHub single-file downloader (one URL or batch file)."
    )
    sub = p.add_subparsers(dest="command", required=True)

    p_one = sub.add_parser("one", help="Download one video by URL")
    p_one.add_argument("url", help="TikTok or Douyin share/video URL")
    p_one.add_argument(
        "-o",
        "--output-dir",
        default=DEFAULT_OUT,
        help=f"Video output directory (default: {DEFAULT_OUT})",
    )
    p_one.add_argument(
        "--no-save-raw",
        action="store_true",
        help="Do not write full raw API response JSON to disk",
    )
    p_one.add_argument(
        "--raw-dir",
        default=DEFAULT_RAW_DIR,
        help=f"Directory for raw API JSON, relative to cwd (default: {DEFAULT_RAW_DIR})",
    )

    p_batch = sub.add_parser("batch", help="Batch download from urls.txt (concurrent, max 10)")
    p_batch.add_argument("urls_file", help="Text file: one URL per line")
    p_batch.add_argument(
        "-o",
        "--output-dir",
        default=DEFAULT_OUT,
        help=f"Video output directory (default: {DEFAULT_OUT})",
    )
    p_batch.add_argument(
        "--no-save-raw",
        action="store_true",
        help="Do not write full raw API response JSON to disk",
    )
    p_batch.add_argument(
        "--raw-dir",
        default=DEFAULT_RAW_DIR,
        help=f"Directory for raw API JSON, relative to cwd (default: {DEFAULT_RAW_DIR})",
    )
    p_batch.add_argument(
        "--max-workers",
        type=int,
        default=10,
        help="Parallel jobs, 1–10 (default 10; hard cap 10)",
    )
    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    save_raw = not args.no_save_raw
    raw_dir = os.path.abspath(os.path.join(os.getcwd(), args.raw_dir))

    if args.command == "one":
        return run_one(
            args.url.strip(),
            os.path.expanduser(args.output_dir),
            raw_dir,
            save_raw,
        )

    if args.command == "batch":
        mw = max(1, min(MAX_WORKERS_CAP, int(args.max_workers)))
        return run_batch(
            args.urls_file,
            os.path.expanduser(args.output_dir),
            mw,
            raw_dir,
            save_raw,
        )

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
```

### Commands (`tikhub_independent.py`)

```bash
# one video; raw JSON -> ./tikhub_api_raw/raw_<aweme_id>_....json
python tikhub_independent.py one "https://www.tiktok.com/@mumumelon67/video/7484120063369415978"

python tikhub_independent.py one "URL" --no-save-raw
python tikhub_independent.py one "URL" --raw-dir ./my_raw_exports

python tikhub_independent.py batch urls.txt
python tikhub_independent.py batch urls.txt --max-workers 4 --raw-dir ./batch_raw
```

Concurrent batch: **API call + optional raw write + download** all run in parallel threads (cap 10). Raw files are written under **`--raw-dir`** (absolute path resolved from cwd).

---

## Part B — `postprocess_tikhub_raw.py` (CSV + simplified JSON)

Save as `postprocess_tikhub_raw.py` (**stdlib only**).  
Input: **one** raw file like `raw_api_response.json`, or a **directory** containing multiple `raw_*.json` / any `*.json` from this workflow.

Output in **`--out-dir`** (default: **current working directory**):

| File | Purpose |
|------|---------|
| `tikhub_videos_summary.csv` | Flat columns for spreadsheets |
| `tikhub_videos_summary.json` | `{"generated_from": ..., "videos": [ {...}, ... ]}` simplified records |

Field selection follows `TikHub_API_数据格式说明.md`: ids, text, type, time, author strip, **statistics**, video basics, music/commerce/AIGC flags where present.

```python
#!/usr/bin/env python3
"""
Post-process TikHub raw API JSON files -> CSV + simplified JSON (stdlib only).

Usage:
  python postprocess_tikhub_raw.py --input raw_api_response.json
  python postprocess_tikhub_raw.py --input ./tikhub_api_raw --out-dir .

See TikHub_API_数据格式说明.md for full field documentation.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from glob import glob
from typing import Any, Dict, List, Optional


def get_aweme_detail(raw: dict) -> Optional[dict]:
    data = raw.get("data") or {}
    d = data.get("aweme_detail")
    if d:
        return d
    ads = data.get("aweme_details")
    if isinstance(ads, list) and ads:
        return ads[0]
    return None


def first_url(obj: Any) -> str:
    if not obj or not isinstance(obj, dict):
        return ""
    lst = obj.get("url_list")
    if isinstance(lst, list) and lst:
        return str(lst[0])
    return ""


def simplify_raw(raw: dict, source_file: str) -> Dict[str, Any]:
    params = raw.get("params") or {}
    share_url = params.get("share_url") or ""
    detail = get_aweme_detail(raw)
    if not detail:
        return {
            "source_file": source_file,
            "request_id": raw.get("request_id"),
            "api_code": raw.get("code"),
            "router": raw.get("router"),
            "share_url": share_url,
            "parse_ok": False,
            "error": "missing data.aweme_detail / aweme_details",
        }

    stats = detail.get("statistics") or {}
    author = detail.get("author") or {}
    video = detail.get("video") or {}
    play = video.get("play_addr") or {}
    music = detail.get("music") or {}
    aigc = detail.get("aigc_info") or {}

    hashtag_names = []
    for x in detail.get("text_extra") or []:
        if isinstance(x, dict) and x.get("hashtag_name"):
            hashtag_names.append(x["hashtag_name"])

    commerce = detail.get("commerce_info")
    commerce_min = None
    if isinstance(commerce, dict):
        commerce_min = {
            k: commerce.get(k)
            for k in ("auction_ad_invited", "ad_source", "adv_promotable")
            if k in commerce
        }
        if not commerce_min:
            commerce_min = {"_present": True}

    row: Dict[str, Any] = {
        "source_file": os.path.basename(source_file),
        "parse_ok": True,
        "request_id": raw.get("request_id"),
        "api_code": raw.get("code"),
        "api_message": raw.get("message"),
        "router": raw.get("router"),
        "share_url": share_url,
        "cache_url": raw.get("cache_url"),
        "aweme_id": detail.get("aweme_id"),
        "desc": (detail.get("desc") or "")[:2000],
        "create_time_unix": detail.get("create_time"),
        "aweme_type": detail.get("aweme_type"),
        "content_desc": detail.get("content_desc") or "",
        "author_nickname": author.get("nickname"),
        "author_unique_id": author.get("unique_id"),
        "author_sec_uid": author.get("sec_uid"),
        "author_uid": author.get("uid"),
        "author_follower_count": author.get("follower_count"),
        "author_following_count": author.get("following_count"),
        "author_aweme_count": author.get("aweme_count"),
        "author_verification_type": author.get("verification_type"),
        "author_account_region": author.get("account_region"),
        "author_commerce_user_level": author.get("commerce_user_level"),
        "digg_count": stats.get("digg_count"),
        "comment_count": stats.get("comment_count"),
        "share_count": stats.get("share_count"),
        "play_count": stats.get("play_count"),
        "collect_count": stats.get("collect_count"),
        "download_count": stats.get("download_count"),
        "forward_count": stats.get("forward_count"),
        "whatsapp_share_count": stats.get("whatsapp_share_count"),
        "video_duration_ms": video.get("duration"),
        "video_width": play.get("width"),
        "video_height": play.get("height"),
        "video_ratio": video.get("ratio"),
        "video_has_watermark": video.get("has_watermark"),
        "cover_url": first_url(video.get("cover")),
        "play_url_sample": first_url(play),
        "music_id": music.get("id_str") or music.get("mid"),
        "music_title": music.get("title"),
        "music_author": music.get("owner_nickname") or music.get("author"),
        "music_is_original_sound": music.get("is_original_sound"),
        "music_is_commerce_music": music.get("is_commerce_music"),
        "music_commercial_right_type": music.get("commercial_right_type"),
        "music_user_count": music.get("user_count"),
        "is_ads": detail.get("is_ads"),
        "commerce_info_min": commerce_min,
        "aigc_created_by_ai": aigc.get("created_by_ai"),
        "aigc_label_type": aigc.get("aigc_label_type"),
        "hashtags": "|".join(hashtag_names) if hashtag_names else "",
    }
    return row


def collect_inputs(path: str) -> List[str]:
    if os.path.isfile(path):
        return [path]
    if os.path.isdir(path):
        files = sorted(glob(os.path.join(path, "*.json")))
        return files
    raise FileNotFoundError(path)


def flatten_for_csv(row: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for k, v in row.items():
        if v is None:
            out[k] = ""
        elif isinstance(v, (dict, list)):
            out[k] = json.dumps(v, ensure_ascii=False)
        else:
            out[k] = v
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Raw TikHub JSON -> CSV + simplified JSON")
    ap.add_argument("--input", "-i", required=True, help="One .json file or a directory of .json")
    ap.add_argument(
        "--out-dir",
        "-o",
        default=".",
        help="Output directory (default: current working directory)",
    )
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
    csv_path = os.path.join(out_dir, "tikhub_videos_summary.csv")
    json_path = os.path.join(out_dir, "tikhub_videos_summary.json")

    videos: List[Dict[str, Any]] = []
    for fp in files:
        try:
            with open(fp, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception as ex:
            videos.append(
                {
                    "source_file": os.path.basename(fp),
                    "parse_ok": False,
                    "error": f"json load: {ex}",
                }
            )
            continue
        videos.append(simplify_raw(raw, fp))

    payload = {
        "generated_from": os.path.abspath(args.input),
        "video_count": len(videos),
        "videos": videos,
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    if videos:
        flat = [flatten_for_csv(v) for v in videos]
        fieldnames: List[str] = sorted({k for row in flat for k in row.keys()})
        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            w.writeheader()
            for row in flat:
                w.writerow({k: row.get(k, "") for k in fieldnames})

    print("Wrote:", csv_path)
    print("Wrote:", json_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

### Commands (`postprocess_tikhub_raw.py`)

From the directory where you want `tikhub_videos_summary.*` (e.g. project root or a report folder):

```bash
python postprocess_tikhub_raw.py --input ./tikhub_api_raw
python postprocess_tikhub_raw.py --input /path/to/raw_api_response.json --out-dir .
```

---

## End-to-end workflow (for others)

1. `pip install httpx`
2. Copy **Part A** and **Part B** scripts next to each other (any folder; **no** dependency on this repo’s Python packages).
3. Run **`tikhub_independent.py`** (`one` or `batch`) so **`tikhub_api_raw/`** (or `--raw-dir`) contains **full** API responses — same idea as `raw_api_response.json`.
4. Run **`postprocess_tikhub_raw.py --input <raw file or dir> --out-dir .`** → get **`tikhub_videos_summary.csv`** and **`tikhub_videos_summary.json`** in the chosen working directory.
5. For field-level meaning of nested keys, open **`TikHub_API_数据格式说明.md`** in this repository (project root).

## Troubleshooting

- **`401/403`**: invalid key or missing scopes.
- **No valid API key configured in this skill**: stop immediately and tell the human user to apply for a paid personal API key at [https://tikhub.io/](https://tikhub.io/), then fill it into `/AWorld/aworld-skills/tikhub_download/SKILL.md` before retrying.
- **`429`**: rate limit; in batch, reduce `--max-workers` or retry later.
- **No `video_url` / parse fail**: video private, removed, or bad URL; a raw file may still be written if the HTTP response was JSON but content incomplete — check `api_code` / `parse_ok` in post-process output.
- **Post-process `parse_ok: false`**: file is not a TikHub `fetch_one_video` payload or damaged JSON.
- **Mainland TikTok**: may need proxy (not in these scripts).

## What this skill does *not* cover

User-profile crawling, non-URL workflows, image-only carousels as first-class exports, and any endpoint other than **`fetch_one_video_by_share_url`**.

---