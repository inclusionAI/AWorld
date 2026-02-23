"""文件操作函数"""

import asyncio
from pathlib import Path
import tempfile
import base64
import mimetypes
from difflib import unified_diff

# 文本检测采样大小（字节）
TEXT_SAMPLE_SIZE = 8192


def format_size(bytes: int) -> str:
    """格式化文件大小"""
    units = ["B", "KB", "MB", "GB", "TB"]
    if bytes == 0:
        return "0 B"
    
    i = 0
    size = float(bytes)
    while size >= 1024 and i < len(units) - 1:
        size /= 1024
        i += 1
    
    return f"{size:.2f} {units[i]}"


async def read_file(path: str) -> str:
    """读取文件内容"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _read_file_sync, path)


def _read_file_sync(path: str) -> str:
    """同步读取文件内容"""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


async def write_file(path: str, content: str) -> None:
    """原子写入文件"""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _write_file_sync, path, content)


def _write_file_sync(path: str, content: str) -> None:
    """同步原子写入文件"""
    dir_path = Path(path).parent
    dir_path.mkdir(parents=True, exist_ok=True)
    
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=dir_path, delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    
    try:
        Path(tmp_path).replace(path)
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise


async def head_file(path: str, num_lines: int) -> str:
    """读取前 N 行"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _head_file_sync, path, num_lines)


def _head_file_sync(path: str, num_lines: int) -> str:
    """同步读取前 N 行"""
    lines = []
    with open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            if i >= num_lines:
                break
            lines.append(line.rstrip("\n\r"))
    return "\n".join(lines)


async def tail_file(path: str, num_lines: int) -> str:
    """读取后 N 行"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _tail_file_sync, path, num_lines)


def _tail_file_sync(path: str, num_lines: int) -> str:
    """同步读取后 N 行"""
    with open(path, "r", encoding="utf-8") as f:
        all_lines = f.readlines()
    return "".join(all_lines[-num_lines:])


def _is_text_file_sync(path: str) -> bool:
    """按内容判断是否为文本：读前 N 字节尝试 UTF-8 解码，成功则视为文本"""
    try:
        with open(path, "rb") as f:
            sample = f.read(TEXT_SAMPLE_SIZE)
        if not sample:
            return True
        sample.decode("utf-8")
        return True
    except (UnicodeDecodeError, OSError):
        return False


async def is_text_file(path: str) -> bool:
    """异步：按内容判断是否为文本文件"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _is_text_file_sync, path)


def _read_file_lines_sync(path: str, start_1based: int, end_1based: int) -> str:
    """同步：读第 start 行到第 end 行（1-based 闭区间）"""
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    # 1-based -> 0-based slice [start-1:end]
    start_idx = max(0, start_1based - 1)
    end_idx = min(len(lines), end_1based)
    if start_idx >= end_idx:
        return ""
    selected = lines[start_idx:end_idx]
    return "".join(selected).rstrip("\n\r")


async def read_file_lines(path: str, start_1based: int, end_1based: int) -> str:
    """读第 start 行到第 end 行（1-based 闭区间）"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, _read_file_lines_sync, path, start_1based, end_1based
    )


def _read_file_binary_sync(path: str) -> bytes:
    """同步读取文件为二进制"""
    with open(path, "rb") as f:
        return f.read()


async def read_file_binary(path: str) -> bytes:
    """读取文件为二进制"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _read_file_binary_sync, path)


def get_mime_and_filename(path: str) -> tuple[str, str]:
    """返回 (mime_type, filename)"""
    mime_type, _ = mimetypes.guess_type(path)
    if not mime_type:
        mime_type = "application/octet-stream"
    filename = Path(path).name
    return mime_type, filename


def _apply_edits_range_sync(path: str, start: int, end: int, new_content: str) -> None:
    """同步：把 content[start:end] 替换为 new_content；start==end 为插入；new_content 空为删除"""
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    if start < 0 or end < start or start > len(content) or end > len(content):
        raise ValueError(f"Invalid range: start={start}, end={end}, file length={len(content)}")
    new_text = content[:start] + new_content + content[end:]
    with open(path, "w", encoding="utf-8") as f:
        f.write(new_text)


async def apply_edits_range(path: str, start: int, end: int, new_content: str) -> None:
    """把文件内容 [start, end) 替换为 new_content；start==end 插入；new_content 空则删除"""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None, _apply_edits_range_sync, path, start, end, new_content
    )


def _copy_file_binary_sync(source: str, target: str) -> None:
    """同步：二进制复制 source -> target，覆盖 target；创建父目录"""
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    with open(source, "rb") as f:
        data = f.read()
    with open(target, "wb") as f:
        f.write(data)


async def copy_file_binary(source: str, target: str) -> None:
    """二进制复制 source -> target"""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _copy_file_binary_sync, source, target)


async def apply_edits(path: str, edits: list[dict], dry_run: bool = False) -> str:
    """应用编辑并返回 diff"""
    content = (await read_file(path)).replace("\r\n", "\n")
    modified = content
    
    for edit in edits:
        old_text = edit["oldText"].replace("\r\n", "\n")
        new_text = edit["newText"].replace("\r\n", "\n")
        
        if old_text in modified:
            modified = modified.replace(old_text, new_text, 1)
            continue
        
        # 行匹配
        old_lines = old_text.split("\n")
        content_lines = modified.split("\n")
        match_found = False
        
        for i in range(len(content_lines) - len(old_lines) + 1):
            potential = content_lines[i : i + len(old_lines)]
            if all(o.strip() == c.strip() for o, c in zip(old_lines, potential)):
                original_indent = content_lines[i][: len(content_lines[i]) - len(content_lines[i].lstrip())] if content_lines[i] else ""
                new_lines = new_text.split("\n")
                if new_lines:
                    new_lines[0] = original_indent + new_lines[0].lstrip()
                content_lines[i : i + len(old_lines)] = new_lines
                modified = "\n".join(content_lines)
                match_found = True
                break
        
        if not match_found:
            raise ValueError(f"Could not find match for edit:\n{edit['oldText']}")
    
    # 生成 diff
    diff_lines = unified_diff(
        content.splitlines(keepends=True),
        modified.splitlines(keepends=True),
        fromfile=path,
        tofile=path,
        lineterm="",
    )
    diff_text = "".join(diff_lines)
    
    num_backticks = 3
    while "`" * num_backticks in diff_text:
        num_backticks += 1
    
    formatted_diff = f"{'`' * num_backticks}diff\n{diff_text}{'`' * num_backticks}\n\n"
    
    if not dry_run:
        await write_file(path, modified)
    
    return formatted_diff


async def read_media_file(path: str) -> tuple[str, str, str]:
    """读取媒体文件，返回 base64、MIME 类型和媒体类型"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _read_media_file_sync, path)


def _read_media_file_sync(path: str) -> tuple[str, str, str]:
    """同步读取媒体文件"""
    mime_type, _ = mimetypes.guess_type(path)
    if not mime_type:
        mime_type = "application/octet-stream"
    
    with open(path, "rb") as f:
        data = base64.b64encode(f.read()).decode("utf-8")
    
    if mime_type.startswith("image/"):
        media_type = "image"
    elif mime_type.startswith("audio/"):
        media_type = "audio"
    else:
        media_type = "blob"
    
    return data, mime_type, media_type


async def get_file_stats(path: str) -> dict:
    """获取文件统计信息"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _get_file_stats_sync, path)


def _get_file_stats_sync(path: str) -> dict:
    """同步获取文件统计信息"""
    stat = Path(path).stat()
    return {
        "size": stat.st_size,
        "created": stat.st_ctime,
        "modified": stat.st_mtime,
        "accessed": stat.st_atime,
        "isDirectory": Path(path).is_dir(),
        "isFile": Path(path).is_file(),
        "permissions": oct(stat.st_mode)[-3:],
    }

