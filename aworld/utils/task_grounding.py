import re
from typing import Any, Iterable

_HANDLE_RE = re.compile(r"(?<!\w)@?[A-Za-z0-9_]{3,32}")
_URL_RE = re.compile(r"https?://[A-Za-z0-9\[][A-Za-z0-9._~:/?#@!$&()*+,;=%\-\[\]]*")
_PATH_RE = re.compile(r"(?:~/|/|\.?/)[^\s`\"'>)，,，。！？；：、”’》」】]+")
_FENCED_CODE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
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


def _strip_fenced_code_blocks(value: str) -> str:
    return _FENCED_CODE_BLOCK_RE.sub(" ", value or "")


def _natural_language_lines(value: str) -> list[str]:
    lines: list[str] = []
    for raw_line in (value or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        lower = line.lower()
        looks_like_command = (
            lower.startswith(("bash:", "bash ", "python ", "python3 ", "node ", "npm ", "npx ", "curl ", "cd "))
            or bool(re.search(r"(^|\s)-{1,2}[A-Za-z][\w-]*(?:\s|=)", line))
        )
        if looks_like_command:
            continue
        lines.append(line)
    return lines


def _looks_like_implementation_path(path: str) -> bool:
    if not path:
        return True

    # Globs and templates are implementation patterns, not concrete task targets.
    if any(marker in path for marker in ("*", "?", "[", "]", "{", "}")):
        return True

    # Bare root-relative fragments usually come from glob expressions such as
    # f"{day_dir}/HealthAutoExport-*.csv", not from a user-selected target.
    if path.startswith("/") and path.count("/") == 1 and "." in path:
        return True

    # A single root-relative word is usually a slash-separated label such as
    # Google/Gemini or a command fragment, not a concrete local artifact.
    if path.startswith("/") and path.count("/") == 1:
        return True

    return False


def _line_declares_path_as_target(line: str, path: str) -> bool:
    if not path or _looks_like_implementation_path(path):
        return False
    if path.startswith(("http://", "https://")):
        return True
    if not path.startswith(("/", "./", "../", "~/")):
        return False
    window = line[max(line.find(path) - 16, 0):line.find(path) + len(path) + 16]
    return bool(re.search(r"(?:目标|文件|目录|路径|保存到|写入|读取|打开|导出到|输出到|本地)", window))


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
        if _looks_like_implementation_path(cleaned):
            return None
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


def _spans_overlap(left: tuple[int, int], right: tuple[int, int]) -> bool:
    return left[0] < right[1] and right[0] < left[1]


def _url_spans(text: str) -> list[tuple[int, int]]:
    return [match.span() for match in _URL_RE.finditer(text or "")]


def extract_path_candidates(value: Any, *, max_paths: int = 12) -> list[str]:
    candidates: list[str] = []

    def visit(node: Any) -> None:
        if len(candidates) >= max(max_paths, 0):
            return
        if isinstance(node, str):
            url_spans = _url_spans(node)
            for match in _PATH_RE.finditer(node):
                if any(_spans_overlap(match.span(), span) for span in url_spans):
                    continue
                path = match.group(0)
                if "/" in path and not path.startswith("//"):
                    candidates.append(path)
                    if len(candidates) >= max(max_paths, 0):
                        return
            return
        if isinstance(node, dict):
            for key, item in node.items():
                visit(key)
                visit(item)
                if len(candidates) >= max(max_paths, 0):
                    return
            return
        if isinstance(node, (list, tuple, set)):
            for item in node:
                visit(item)
                if len(candidates) >= max(max_paths, 0):
                    return

    visit(value)
    return _dedupe_preserve_order(candidates)[:max(max_paths, 0)]


def extract_required_anchors(request: str | None, *, max_anchors: int = 8) -> list[str]:
    if not request:
        return []

    request_without_code = _strip_fenced_code_blocks(request)
    natural_lines = _natural_language_lines(request_without_code)
    normalized_request = _normalize_whitespace("\n".join(natural_lines))
    anchors: list[str] = []

    url_spans = _url_spans(normalized_request)

    for match in _URL_RE.finditer(normalized_request):
        anchors.append(match.group(0))

    for line in natural_lines:
        line_url_spans = _url_spans(line)
        for match in _PATH_RE.finditer(line):
            if any(_spans_overlap(match.span(), span) for span in line_url_spans):
                continue
            path = match.group(0)
            if "/" in path and not path.startswith("//") and _line_declares_path_as_target(line, path):
                anchors.append(path)

    for match in _HANDLE_RE.finditer(normalized_request):
        token = match.group(0)
        if token.startswith("@"):
            anchors.append(token)
        elif re.search(
            r"(账号|用户|作者|from:|@)",
            normalized_request[max(match.start() - 8, 0):match.end() + 8],
            re.IGNORECASE,
        ):
            anchors.append(token)

    for pattern in _QUOTED_PATTERNS:
        anchors.extend(group for group in pattern.findall(request_without_code) if isinstance(group, str))

    for pattern in _TOPIC_PATTERNS:
        for match in pattern.finditer(request_without_code):
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
