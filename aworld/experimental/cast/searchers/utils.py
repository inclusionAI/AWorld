"""
Ripgrep管理器和工具集成
=====================

基于Ripgrep实现，提供高性能的文本搜索和文件发现能力。
同时提供基于Python的Pygrep实现作为替代方案。
"""

import asyncio
import fnmatch
import json
import os
import platform
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Any

import requests

from ..utils import logger


@dataclass
class RipgrepMatch:
    """Ripgrep搜索匹配结果"""
    file_path: str
    line_number: int
    line_text: str
    absolute_offset: int
    submatches: List[Dict[str, Any]]
    mod_time: float = 0.0


@dataclass
class RipgrepStats:
    """Ripgrep搜索统计信息"""
    elapsed_secs: float
    searches: int
    searches_with_match: int
    bytes_searched: int
    bytes_printed: int
    matched_lines: int
    matches: int


class RipgrepManager:
    """
    Ripgrep二进制文件管理器

    负责自动下载、安装和管理Ripgrep二进制文件。
    基于opencode的跨平台支持实现。
    """

    PLATFORM_CONFIG = {
        ("aarch64", "Darwin"): {
            "platform": "aarch64-apple-darwin",
            "extension": "tar.gz"
        },
        ("aarch64", "Linux"): {
            "platform": "aarch64-unknown-linux-gnu",
            "extension": "tar.gz"
        },
        ("x86_64", "Darwin"): {
            "platform": "x86_64-apple-darwin",
            "extension": "tar.gz"
        },
        ("x86_64", "Linux"): {
            "platform": "x86_64-unknown-linux-musl",
            "extension": "tar.gz"
        },
        ("AMD64", "Windows"): {
            "platform": "x86_64-pc-windows-msvc",
            "extension": "zip"
        }
    }

    def __init__(self, install_dir: Optional[Path] = None):
        self.install_dir = install_dir or Path.home() / ".aworld" / "bin"
        self.version = "14.1.1"
        self._executable_path: Optional[Path] = None

        # 确保安装目录存在
        self.install_dir.mkdir(parents=True, exist_ok=True)

    @property
    def executable_path(self) -> Path:
        """获取Ripgrep可执行文件路径"""
        if self._executable_path:
            return self._executable_path

        # 检查系统是否已安装ripgrep
        system_rg = shutil.which("rg")
        if system_rg:
            self._executable_path = Path(system_rg)
            return self._executable_path

        # 检查本地安装
        exe_name = "rg.exe" if platform.system() == "Windows" else "rg"
        local_path = self.install_dir / exe_name

        if local_path.exists():
            self._executable_path = local_path
            return self._executable_path

        # 需要下载安装
        raise RuntimeError("Ripgrep未安装，请调用install()方法安装")

    def is_installed(self) -> bool:
        """检查Ripgrep是否已安装"""
        try:
            self.executable_path
            return True
        except RuntimeError:
            return False

    async def install(self) -> Path:
        """异步安装Ripgrep"""
        logger.info(f"开始安装Ripgrep {self.version}")

        # 获取平台配置
        machine = platform.machine()
        system = platform.system()
        platform_key = (machine, system)

        if platform_key not in self.PLATFORM_CONFIG:
            raise RuntimeError(f"不支持的平台: {machine}-{system}")

        config = self.PLATFORM_CONFIG[platform_key]
        filename = f"ripgrep-{self.version}-{config['platform']}.{config['extension']}"
        download_url = f"https://github.com/BurntSushi/ripgrep/releases/download/{self.version}/{filename}"

        logger.info(f"下载URL: {download_url}")

        # 下载文件
        temp_file = await self._download_file(download_url, filename)

        try:
            # 解压和安装
            exe_name = "rg.exe" if system == "Windows" else "rg"
            target_path = self.install_dir / exe_name

            if config['extension'] == 'tar.gz':
                await self._extract_tar_gz(temp_file, target_path)
            elif config['extension'] == 'zip':
                await self._extract_zip(temp_file, target_path)

            # 设置可执行权限 (Unix系统)
            if system != "Windows":
                target_path.chmod(0o755)

            self._executable_path = target_path
            logger.info(f"Ripgrep安装完成: {target_path}")
            return target_path

        finally:
            # 清理临时文件
            temp_file.unlink(missing_ok=True)

    async def _download_file(self, url: str, filename: str) -> Path:
        """异步下载文件"""
        temp_dir = Path(tempfile.gettempdir())
        temp_file = temp_dir / filename

        def download():
            response = requests.get(url, stream=True)
            response.raise_for_status()

            with open(temp_file, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)

        # 在线程池中执行下载
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, download)

        return temp_file

    async def _extract_tar_gz(self, archive_path: Path, target_path: Path):
        """解压tar.gz文件"""
        def extract():
            import tarfile
            with tarfile.open(archive_path, 'r:gz') as tar:
                # 查找rg可执行文件
                for member in tar.getmembers():
                    if member.name.endswith('/rg') or member.name == 'rg':
                        with tar.extractfile(member) as f:
                            target_path.write_bytes(f.read())
                        return
                raise RuntimeError("在压缩包中未找到rg可执行文件")

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, extract)

    async def _extract_zip(self, archive_path: Path, target_path: Path):
        """解压zip文件"""
        def extract():
            with zipfile.ZipFile(archive_path, 'r') as zip_file:
                # 查找rg.exe文件
                for file_name in zip_file.namelist():
                    if file_name.endswith('rg.exe'):
                        with zip_file.open(file_name) as f:
                            target_path.write_bytes(f.read())
                        return
                raise RuntimeError("在压缩包中未找到rg.exe文件")

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, extract)


class RipgrepSearcher:
    """
    Ripgrep搜索器

    提供高级的搜索接口，包括内容搜索、文件发现和目录树生成。
    """

    def __init__(self, manager: Optional[RipgrepManager] = None):
        self.manager = manager or RipgrepManager()

    async def ensure_installed(self):
        """确保Ripgrep已安装"""
        if not self.manager.is_installed():
            await self.manager.install()

    async def search(self,
                    pattern: str,
                    path: str = ".",
                    include_patterns: Optional[List[str]] = None,
                    max_count: Optional[int] = None,
                    context_lines: int = 0,
                    case_sensitive: bool = False,
                    follow_symlinks: bool = True,
                    search_hidden: bool = True) -> List[RipgrepMatch]:
        """
        执行内容搜索

        Args:
            pattern: 搜索模式(正则表达式)
            path: 搜索路径
            include_patterns: 包含文件模式列表
            max_count: 最大匹配数
            context_lines: 上下文行数
            case_sensitive: 是否大小写敏感
            follow_symlinks: 是否跟随符号链接
            search_hidden: 是否搜索隐藏文件

        Returns:
            匹配结果列表
        """
        await self.ensure_installed()

        args = [
            str(self.manager.executable_path),
            "--json",
            "--regexp", pattern
        ]

        # 基本选项
        if not case_sensitive:
            args.append("--ignore-case")
        if follow_symlinks:
            args.append("--follow")
        if search_hidden:
            args.append("--hidden")

        # 排除Git目录
        args.extend(["--glob", "!.git/*"])

        # 包含模式
        if include_patterns:
            for pattern_str in include_patterns:
                args.extend(["--glob", pattern_str])

        # 最大计数
        if max_count:
            args.append(f"--max-count={max_count}")

        # 上下文行数
        if context_lines > 0:
            args.append(f"--context={context_lines}")

        args.append(path)

        logger.debug(f"执行Ripgrep搜索: {' '.join(args)}")

        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await process.communicate()

            if process.returncode not in (0, 1):  # 0=找到匹配, 1=无匹配, 2+=错误
                error_msg = stderr.decode('utf-8', errors='ignore')
                raise RuntimeError(f"Ripgrep搜索失败: {error_msg}")

            return await self._parse_json_output(stdout.decode('utf-8', errors='ignore'))

        except Exception as e:
            logger.error(f"Ripgrep搜索异常: {e}")
            raise

    async def find_files(self,
                        path: str = ".",
                        include_patterns: Optional[List[str]] = None,
                        max_depth: Optional[int] = None,
                        follow_symlinks: bool = True,
                        search_hidden: bool = True) -> List[str]:
        """
        发现文件

        Args:
            path: 搜索路径
            include_patterns: 包含文件模式
            max_depth: 最大搜索深度
            follow_symlinks: 是否跟随符号链接
            search_hidden: 是否包含隐藏文件

        Returns:
            文件路径列表
        """
        await self.ensure_installed()

        args = [
            str(self.manager.executable_path),
            "--files"
        ]

        # 基本选项
        if follow_symlinks:
            args.append("--follow")
        if search_hidden:
            args.append("--hidden")

        # 排除Git目录
        args.extend(["--glob", "!.git/*"])

        # 包含模式
        if include_patterns:
            for pattern in include_patterns:
                args.extend(["--glob", pattern])

        # 最大深度
        if max_depth is not None:
            args.append(f"--max-depth={max_depth}")

        args.append(path)

        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                error_msg = stderr.decode('utf-8', errors='ignore')
                logger.warning(f"文件发现警告: {error_msg}")

            output = stdout.decode('utf-8', errors='ignore')
            return [line.strip() for line in output.split('\n') if line.strip()]

        except Exception as e:
            logger.error(f"文件发现异常: {e}")
            raise

    async def _parse_json_output(self, output: str) -> List[RipgrepMatch]:
        """解析JSON输出"""
        matches = []

        for line in output.strip().split('\n'):
            if not line:
                continue

            try:
                data = json.loads(line)
                if data.get('type') == 'match':
                    match_data = data['data']

                    # 获取文件修改时间
                    file_path = match_data['path']['text']
                    try:
                        mod_time = os.path.getmtime(file_path)
                    except OSError:
                        mod_time = 0.0

                    match = RipgrepMatch(
                        file_path=file_path,
                        line_number=match_data['line_number'],
                        line_text=match_data['lines']['text'],
                        absolute_offset=match_data['absolute_offset'],
                        submatches=match_data.get('submatches', []),
                        mod_time=mod_time
                    )
                    matches.append(match)

            except json.JSONDecodeError as e:
                logger.warning(f"解析JSON行失败: {line[:100]}... 错误: {e}")
                continue

        # 按修改时间排序
        matches.sort(key=lambda m: m.mod_time, reverse=True)
        return matches


class PygrepSearcher:
    """
    Python实现的Grep搜索器
    
    使用Python的re模块和文件遍历实现文本搜索功能，作为Ripgrep的替代方案。
    提供与RipgrepSearcher相同的接口，可以无缝替换。
    """

    def __init__(self):
        """初始化Pygrep搜索器"""
        pass

    async def ensure_installed(self):
        """确保搜索器可用（Python实现无需安装）"""
        pass

    def _should_include_file(self, file_path: Path, include_patterns: Optional[List[str]] = None) -> bool:
        """检查文件是否应该被包含在搜索中"""
        # 排除 .git 目录
        if '.git' in file_path.parts:
            return False
        
        # 如果没有指定包含模式，包含所有文件
        if not include_patterns:
            return True
        
        # 检查文件是否匹配任何包含模式
        file_str = str(file_path)
        for pattern in include_patterns:
            # 支持 glob 模式匹配
            if fnmatch.fnmatch(file_str, pattern) or fnmatch.fnmatch(file_path.name, pattern):
                return True
        
        return False

    def _is_binary_file(self, file_path: Path) -> bool:
        """检测文件是否为二进制文件"""
        try:
            # 检查文件扩展名
            binary_extensions = {
                '.zip', '.tar', '.gz', '.exe', '.dll', '.so', '.class', '.jar',
                '.war', '.7z', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
                '.odt', '.ods', '.odp', '.bin', '.dat', '.obj', '.o', '.a',
                '.lib', '.wasm', '.pyc', '.pyo', '.png', '.jpg', '.jpeg', '.gif',
                '.bmp', '.ico', '.svg', '.pdf', '.mp3', '.mp4', '.avi', '.mov'
            }
            if file_path.suffix.lower() in binary_extensions:
                return True
            
            # 检查文件内容
            try:
                with open(file_path, 'rb') as f:
                    chunk = f.read(4096)
                    if b'\x00' in chunk:
                        return True
                    # 检查非打印字符比例
                    non_printable = sum(1 for byte in chunk if byte < 9 or (byte > 13 and byte < 32))
                    if len(chunk) > 0 and (non_printable / len(chunk)) > 0.3:
                        return True
            except Exception:
                return True
        except Exception:
            return True
        
        return False

    async def search(self,
                    pattern: str,
                    path: str = ".",
                    include_patterns: Optional[List[str]] = None,
                    max_count: Optional[int] = None,
                    context_lines: int = 0,
                    case_sensitive: bool = False,
                    follow_symlinks: bool = True,
                    search_hidden: bool = True) -> List[RipgrepMatch]:
        """
        执行内容搜索

        Args:
            pattern: 搜索模式(正则表达式)
            path: 搜索路径
            include_patterns: 包含文件模式列表
            max_count: 最大匹配数
            context_lines: 上下文行数
            case_sensitive: 是否大小写敏感
            follow_symlinks: 是否跟随符号链接
            search_hidden: 是否搜索隐藏文件

        Returns:
            匹配结果列表
        """
        search_path = Path(path)
        if not search_path.exists():
            raise ValueError(f"搜索路径不存在: {path}")

        # 编译正则表达式
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            regex = re.compile(pattern, flags)
        except re.error as e:
            raise ValueError(f"无效的正则表达式模式: {pattern}, 错误: {e}")

        matches = []
        match_count = 0

        # 遍历文件
        def search_files():
            nonlocal match_count
            for root, dirs, files in os.walk(search_path, followlinks=follow_symlinks):
                # 过滤目录
                dirs[:] = [d for d in dirs if search_hidden or not d.startswith('.')]
                
                for file_name in files:
                    # 跳过隐藏文件
                    if not search_hidden and file_name.startswith('.'):
                        continue
                    
                    file_path = Path(root) / file_name
                    
                    # 检查是否应该包含此文件
                    if not self._should_include_file(file_path, include_patterns):
                        continue
                    
                    # 跳过二进制文件
                    if self._is_binary_file(file_path):
                        continue
                    
                    # 检查是否达到最大匹配数
                    if max_count and match_count >= max_count:
                        return
                    
                    try:
                        # 读取文件内容
                        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                            lines = f.readlines()
                            absolute_offset = 0
                            
                            for line_num, line in enumerate(lines, start=1):
                                # 检查是否达到最大匹配数
                                if max_count and match_count >= max_count:
                                    break
                                
                                line_text = line.rstrip('\n\r')
                                
                                # 搜索匹配
                                for match in regex.finditer(line_text):
                                    # 提取子匹配
                                    submatches = []
                                    for i, group in enumerate(match.groups(), start=1):
                                        if group is not None:
                                            submatches.append({
                                                'start': match.start(i),
                                                'end': match.end(i),
                                                'match': {'text': group}
                                            })
                                    
                                    # 添加主匹配
                                    submatches.insert(0, {
                                        'start': match.start(),
                                        'end': match.end(),
                                        'match': {'text': match.group()}
                                    })
                                    
                                    # 获取文件修改时间
                                    try:
                                        mod_time = os.path.getmtime(file_path)
                                    except OSError:
                                        mod_time = 0.0
                                    
                                    match_obj = RipgrepMatch(
                                        file_path=str(file_path),
                                        line_number=line_num,
                                        line_text=line_text,
                                        absolute_offset=absolute_offset + match.start(),
                                        submatches=submatches,
                                        mod_time=mod_time
                                    )
                                    matches.append(match_obj)
                                    match_count += 1
                                
                                # 更新绝对偏移量（包括换行符）
                                absolute_offset += len(line.encode('utf-8'))
                                
                    except (UnicodeDecodeError, PermissionError, OSError) as e:
                        logger.debug(f"跳过文件 {file_path}: {e}")
                        continue

        # 在线程池中执行搜索
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, search_files)

        # 按修改时间排序
        matches.sort(key=lambda m: m.mod_time, reverse=True)
        
        logger.debug(f"Pygrep搜索完成: pattern='{pattern}', 找到 {len(matches)} 个匹配")
        return matches

    async def find_files(self,
                        path: str = ".",
                        include_patterns: Optional[List[str]] = None,
                        max_depth: Optional[int] = None,
                        follow_symlinks: bool = True,
                        search_hidden: bool = True) -> List[str]:
        """
        发现文件

        Args:
            path: 搜索路径
            include_patterns: 包含文件模式
            max_depth: 最大搜索深度
            follow_symlinks: 是否跟随符号链接
            search_hidden: 是否包含隐藏文件

        Returns:
            文件路径列表
        """
        search_path = Path(path)
        if not search_path.exists():
            raise ValueError(f"搜索路径不存在: {path}")

        file_paths = []

        def find_files_recursive(current_path: Path, current_depth: int = 0):
            # 检查深度限制
            if max_depth is not None and current_depth > max_depth:
                return
            
            try:
                # 遍历当前目录
                for item in current_path.iterdir():
                    # 跳过隐藏文件/目录
                    if not search_hidden and item.name.startswith('.'):
                        continue
                    
                    # 排除 .git 目录
                    if item.name == '.git' and item.is_dir():
                        continue
                    
                    # 处理符号链接
                    if item.is_symlink():
                        if not follow_symlinks:
                            continue
                        try:
                            item = item.resolve()
                        except (OSError, RuntimeError):
                            continue
                    
                    if item.is_file():
                        # 检查是否匹配包含模式
                        if self._should_include_file(item, include_patterns):
                            file_paths.append(str(item))
                    elif item.is_dir():
                        find_files_recursive(item, current_depth + 1)
            
            except (PermissionError, OSError) as e:
                logger.debug(f"无法访问目录 {current_path}: {e}")

        # 在线程池中执行文件查找
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, find_files_recursive, search_path, 0)

        logger.debug(f"Pygrep文件查找完成: 找到 {len(file_paths)} 个文件")
        return file_paths