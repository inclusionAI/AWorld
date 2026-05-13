import re
from typing import Iterable

_HANDLE_RE = re.compile(r"(?<!\w)@?[A-Za-z0-9_]{3,32}")
_URL_RE = re.compile(r"https?://[^\s)>\]\"']+")
_PATH_RE = re.compile(r"(?:~/|/|\.?/)[^\s`\"'>)]+")
_QUOTED_PATTERNS = (
    re.compile(r"[\"“”'`《》「」](.{4,120}?)[\"“”'`《》「」]"),
)
_TOPIC_PATTERNS = (
    re.compile(r"其中([^。！？\n]{4,100}?)(?:主题的|主题)"),
    re.compile(r"(?:标题为|题为|名为|叫做)([^。！？\n]{3,100})"),
    re.compile(r"(?:about|titled|called)\s+[\"“”'`]?(.*?)(?:[\"“”'`]|$)", re.IGNORECASE),
)
_COMMON_NOISE = {
    "obsidian",
    "x",
    "twitter",
    "帖子",
    "推文",
    "文章",
    "文档",
    "文件",
    "知识库",
}


def _normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _canonical_match_text(value: str) -> str:
    lowered = value.lower()
    return re.sub(r"[\s\-_.,，。:：;；!！?？'\"“”`《》「」（）()\[\]{}]+", "", lowered)


def _clean_anchor(value: str) -> str | None:
    if not value:
        return None
    cleaned = _normalize_whitespace(value).strip("`'\"“”《》「」,，。:：;；")
    if not cleaned:
        return None
    if cleaned.lower() in _COMMON_NOISE:
        return None
    if cleaned.startswith("@"):
        return cleaned
    if cleaned.startswith(("http://", "https://", "/", "./", "../", "~/")):
        return cleaned
    if len(cleaned) < 4:
        return None
    return cleaned


def _dedupe_preserve_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        cleaned = _clean_anchor(value)
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(cleaned)
    return deduped


def extract_required_anchors(request: str | None, *, max_anchors: int = 8) -> list[str]:
    if not request:
        return []

    request = _normalize_whitespace(request)
    anchors: list[str] = []

    for match in _URL_RE.finditer(request):
        anchors.append(match.group(0))

    for match in _PATH_RE.finditer(request):
        path = match.group(0)
        if "/" in path:
            anchors.append(path)

    for match in _HANDLE_RE.finditer(request):
        token = match.group(0)
        if token.startswith("@"):
            anchors.append(token)
        elif re.search(r"(账号|用户|作者|from:|@)", request[max(match.start() - 8, 0):match.end() + 8], re.IGNORECASE):
            anchors.append(token)

    for pattern in _QUOTED_PATTERNS:
        anchors.extend(group for group in pattern.findall(request) if isinstance(group, str))

    for pattern in _TOPIC_PATTERNS:
        for match in pattern.finditer(request):
            candidate = match.group(1) if match.groups() else match.group(0)
            anchors.append(candidate)

    return _dedupe_preserve_order(anchors)[:max(max_anchors, 0)]


def anchor_matches_text(anchor: str, text: str | None) -> bool:
    if not anchor or not text:
        return False

    anchor_lower = anchor.lower()
    text_lower = text.lower()
    if anchor.startswith(("http://", "https://", "/", "./", "../", "~/", "@")):
        return anchor_lower in text_lower

    return _canonical_match_text(anchor) in _canonical_match_text(text)

