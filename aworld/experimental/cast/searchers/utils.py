"""
Ripgrep manager and tool integration
=====================

Provides high-performance text search and file discovery based on Ripgrep.
Also provides a Python-based Pygrep implementation as an alternative implementation.
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
    """Ripgrep search match result"""
    file_path: str
    line_number: int
    line_text: str
    absolute_offset: int
    submatches: List[Dict[str, Any]]
    mod_time: float = 0.0


@dataclass
class RipgrepStats:
    """Ripgrep search statistics"""
    elapsed_secs: float
    searches: int
    searches_with_match: int
    bytes_searched: int
    bytes_printed: int
    matched_lines: int
    matches: int


class RipgrepManager:
    """
    Ripgrep binary manager

    Responsible for automatically downloading, installing and managing the Ripgrep binary.
    Based on opencode's cross‑platform support implementation.
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

        # Ensure the installation directory exists
        self.install_dir.mkdir(parents=True, exist_ok=True)

    @property
    def executable_path(self) -> Path:
        """Get the path to the Ripgrep executable"""
        if self._executable_path:
            return self._executable_path

        # Check whether ripgrep is already installed on the system
        system_rg = shutil.which("rg")
        if system_rg:
            self._executable_path = Path(system_rg)
            return self._executable_path

        # Check local installation directory
        exe_name = "rg.exe" if platform.system() == "Windows" else "rg"
        local_path = self.install_dir / exe_name

        if local_path.exists():
            self._executable_path = local_path
            return self._executable_path

        # Need to download and install
        raise RuntimeError("Ripgrep is not installed, please call install() first")

    def is_installed(self) -> bool:
        """Check whether Ripgrep is installed"""
        try:
            self.executable_path
            return True
        except RuntimeError:
            return False

    async def install(self) -> Path:
        """Asynchronously install Ripgrep"""
        logger.info(f"Start installing Ripgrep {self.version}")

        # Get platform configuration
        machine = platform.machine()
        system = platform.system()
        platform_key = (machine, system)

        if platform_key not in self.PLATFORM_CONFIG:
            raise RuntimeError(f"Unsupported platform: {machine}-{system}")

        config = self.PLATFORM_CONFIG[platform_key]
        filename = f"ripgrep-{self.version}-{config['platform']}.{config['extension']}"
        download_url = f"https://github.com/BurntSushi/ripgrep/releases/download/{self.version}/{filename}"

        logger.info(f"Download URL: {download_url}")

        # Download archive file
        temp_file = await self._download_file(download_url, filename)

        try:
            # Extract and install
            exe_name = "rg.exe" if system == "Windows" else "rg"
            target_path = self.install_dir / exe_name

            if config['extension'] == 'tar.gz':
                await self._extract_tar_gz(temp_file, target_path)
            elif config['extension'] == 'zip':
                await self._extract_zip(temp_file, target_path)

            # Set executable permission (Unix‑like systems)
            if system != "Windows":
                target_path.chmod(0o755)

            self._executable_path = target_path
            logger.info(f"Ripgrep installed at: {target_path}")
            return target_path

        finally:
            # Clean up temporary file
            temp_file.unlink(missing_ok=True)

    async def _download_file(self, url: str, filename: str) -> Path:
        """Download a file asynchronously"""
        temp_dir = Path(tempfile.gettempdir())
        temp_file = temp_dir / filename

        def download():
            response = requests.get(url, stream=True)
            response.raise_for_status()

            with open(temp_file, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)

        # Run the download in a thread pool
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, download)

        return temp_file

    async def _extract_tar_gz(self, archive_path: Path, target_path: Path):
        """Extract a tar.gz archive"""
        def extract():
            import tarfile
            with tarfile.open(archive_path, 'r:gz') as tar:
                # Look for the rg executable in archive
                for member in tar.getmembers():
                    if member.name.endswith('/rg') or member.name == 'rg':
                        with tar.extractfile(member) as f:
                            target_path.write_bytes(f.read())
                        return
                raise RuntimeError("rg executable not found in archive")

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, extract)

    async def _extract_zip(self, archive_path: Path, target_path: Path):
        """Extract a zip archive"""
        def extract():
            with zipfile.ZipFile(archive_path, 'r') as zip_file:
                # Look for rg.exe in archive
                for file_name in zip_file.namelist():
                    if file_name.endswith('rg.exe'):
                        with zip_file.open(file_name) as f:
                            target_path.write_bytes(f.read())
                        return
                raise RuntimeError("rg.exe not found in archive")

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, extract)


class RipgrepSearcher:
    """
    Ripgrep searcher

    Provides high‑level search APIs, including content search, file discovery and directory tree generation.
    """

    def __init__(self, manager: Optional[RipgrepManager] = None):
        self.manager = manager or RipgrepManager()

    async def ensure_installed(self):
        """Ensure Ripgrep is installed"""
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
        Execute a content search.

        Args:
            pattern: Search pattern (regular expression).
            path: Search path.
            include_patterns: List of file glob patterns to include.
            max_count: Maximum number of matches.
            context_lines: Number of context lines.
            case_sensitive: Whether the search is case‑sensitive.
            follow_symlinks: Whether to follow symbolic links.
            search_hidden: Whether to search hidden files.

        Returns:
            List of match results.
        """
        await self.ensure_installed()

        args = [
            str(self.manager.executable_path),
            "--json",
            "--regexp", pattern
        ]

        # Basic options
        if not case_sensitive:
            args.append("--ignore-case")
        if follow_symlinks:
            args.append("--follow")
        if search_hidden:
            args.append("--hidden")

        # Exclude Git directory
        args.extend(["--glob", "!.git/*"])

        # Include patterns
        if include_patterns:
            for pattern_str in include_patterns:
                args.extend(["--glob", pattern_str])

        # Maximum number of matches
        if max_count:
            args.append(f"--max-count={max_count}")

        # Number of context lines
        if context_lines > 0:
            args.append(f"--context={context_lines}")

        args.append(path)

        logger.debug(f"Run Ripgrep search: {' '.join(args)}")

        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await process.communicate()

            if process.returncode not in (0, 1):  # 0 = match found, 1 = no match, >=2 = error
                error_msg = stderr.decode('utf-8', errors='ignore')
                raise RuntimeError(f"Ripgrep search failed: {error_msg}")

            return await self._parse_json_output(stdout.decode('utf-8', errors='ignore'))

        except Exception as e:
            logger.error(f"Ripgrep search error: {e}")
            raise

    async def find_files(self,
                        path: str = ".",
                        include_patterns: Optional[List[str]] = None,
                        max_depth: Optional[int] = None,
                        follow_symlinks: bool = True,
                        search_hidden: bool = True) -> List[str]:
        """
        Discover files.

        Args:
            path: Root search path.
            include_patterns: File glob patterns to include.
            max_depth: Maximum directory traversal depth.
            follow_symlinks: Whether to follow symbolic links.
            search_hidden: Whether to include hidden files.

        Returns:
            List of file paths.
        """
        await self.ensure_installed()

        args = [
            str(self.manager.executable_path),
            "--files"
        ]

        # Basic options
        if follow_symlinks:
            args.append("--follow")
        if search_hidden:
            args.append("--hidden")

        # Exclude Git directory
        args.extend(["--glob", "!.git/*"])

        # Include patterns
        if include_patterns:
            for pattern in include_patterns:
                args.extend(["--glob", pattern])

        # Maximum depth
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
                logger.warning(f"File discovery warning: {error_msg}")

            output = stdout.decode('utf-8', errors='ignore')
            return [line.strip() for line in output.split('\n') if line.strip()]

        except Exception as e:
            logger.error(f"File discovery error: {e}")
            raise

    async def _parse_json_output(self, output: str) -> List[RipgrepMatch]:
        """Parse ripgrep JSON output"""
        matches = []

        for line in output.strip().split('\n'):
            if not line:
                continue

            try:
                data = json.loads(line)
                if data.get('type') == 'match':
                    match_data = data['data']

                    # Get file modification time
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
                logger.warning(f"Failed to parse JSON line: {line[:100]}... error: {e}")
                continue

        # Sort by modification time
        matches.sort(key=lambda m: m.mod_time, reverse=True)
        return matches


class PygrepSearcher:
    """
    Grep‑like searcher implemented in pure Python.

    Uses Python's ``re`` module and filesystem traversal to implement text search
    as a fallback when Ripgrep is not available.
    Provides the same interface as ``RipgrepSearcher`` and can be used as a drop‑in replacement.
    """

    def __init__(self):
        """Initialize the Pygrep searcher"""
        pass

    async def ensure_installed(self):
        """Ensure the searcher is available (Python implementation needs no installation)"""
        pass

    def _should_include_file(self, file_path: Path, include_patterns: Optional[List[str]] = None) -> bool:
        """Check whether a file should be included in the search"""
        # Exclude .git directory
        if '.git' in file_path.parts:
            return False
        
        # If no include patterns are specified, include all files
        if not include_patterns:
            return True
        
        # Check whether the file matches any include pattern
        file_str = str(file_path)
        for pattern in include_patterns:
            # Support glob‑style matching
            if fnmatch.fnmatch(file_str, pattern) or fnmatch.fnmatch(file_path.name, pattern):
                return True
        
        return False

    def _is_binary_file(self, file_path: Path) -> bool:
        """Detect whether the file is binary"""
        try:
            # First check by file extension
            binary_extensions = {
                '.zip', '.tar', '.gz', '.exe', '.dll', '.so', '.class', '.jar',
                '.war', '.7z', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
                '.odt', '.ods', '.odp', '.bin', '.dat', '.obj', '.o', '.a',
                '.lib', '.wasm', '.pyc', '.pyo', '.png', '.jpg', '.jpeg', '.gif',
                '.bmp', '.ico', '.svg', '.pdf', '.mp3', '.mp4', '.avi', '.mov'
            }
            if file_path.suffix.lower() in binary_extensions:
                return True
            
            # Then check by sampling file content
            try:
                with open(file_path, 'rb') as f:
                    chunk = f.read(4096)
                    if b'\x00' in chunk:
                        return True
                    # Check ratio of non‑printable characters
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
        Execute a content search.

        Args:
            pattern: Search pattern (regular expression).
            path: Search path.
            include_patterns: List of file glob patterns to include.
            max_count: Maximum number of matches.
            context_lines: Number of context lines (currently unused).
            case_sensitive: Whether the search is case‑sensitive.
            follow_symlinks: Whether to follow symbolic links.
            search_hidden: Whether to search hidden files.

        Returns:
            List of match results.
        """
        search_path = Path(path)
        if not search_path.exists():
            raise ValueError(f"Search path does not exist: {path}")

        # Compile regular expression
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            regex = re.compile(pattern, flags)
        except re.error as e:
            raise ValueError(f"Invalid regular expression pattern: {pattern}, error: {e}")

        matches = []
        match_count = 0

        # Traverse files
        def search_files():
            nonlocal match_count
            for root, dirs, files in os.walk(search_path, followlinks=follow_symlinks):
                # Filter directories
                dirs[:] = [d for d in dirs if search_hidden or not d.startswith('.')]
                
                for file_name in files:
                    # Skip hidden files if required
                    if not search_hidden and file_name.startswith('.'):
                        continue
                    
                    file_path = Path(root) / file_name
                    
                    # Check whether this file should be included
                    if not self._should_include_file(file_path, include_patterns):
                        continue
                    
                    # Skip binary files
                    if self._is_binary_file(file_path):
                        continue
                    
                    # Stop when reaching the maximum number of matches
                    if max_count and match_count >= max_count:
                        return
                    
                    try:
                        # Read file content
                        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                            lines = f.readlines()
                            absolute_offset = 0
                            
                            for line_num, line in enumerate(lines, start=1):
                                # Check again if we have reached the maximum number of matches
                                if max_count and match_count >= max_count:
                                    break
                                
                                line_text = line.rstrip('\n\r')
                                
                                # Search for matches in this line
                                for match in regex.finditer(line_text):
                                    # Extract sub‑matches for capturing groups
                                    submatches = []
                                    for i, group in enumerate(match.groups(), start=1):
                                        if group is not None:
                                            submatches.append({
                                                'start': match.start(i),
                                                'end': match.end(i),
                                                'match': {'text': group}
                                            })
                                    
                                    # Add the main match at the beginning
                                    submatches.insert(0, {
                                        'start': match.start(),
                                        'end': match.end(),
                                        'match': {'text': match.group()}
                                    })
                                    
                                    # Get file modification time
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
                                
                                # Update absolute byte offset (including newline bytes)
                                absolute_offset += len(line.encode('utf-8'))
                                
                    except (UnicodeDecodeError, PermissionError, OSError) as e:
                        logger.debug(f"Skip file {file_path}: {e}")
                        continue

        # Run the search in a thread pool
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, search_files)

        # Sort by modification time
        matches.sort(key=lambda m: m.mod_time, reverse=True)
        
        logger.debug(f"Pygrep search finished: pattern='{pattern}', found {len(matches)} matches")
        return matches

    async def find_files(self,
                        path: str = ".",
                        include_patterns: Optional[List[str]] = None,
                        max_depth: Optional[int] = None,
                        follow_symlinks: bool = True,
                        search_hidden: bool = True) -> List[str]:
        """
        Discover files.

        Args:
            path: Root search path.
            include_patterns: File glob patterns to include.
            max_depth: Maximum directory traversal depth.
            follow_symlinks: Whether to follow symbolic links.
            search_hidden: Whether to include hidden files.

        Returns:
            List of file paths.
        """
        search_path = Path(path)
        if not search_path.exists():
            raise ValueError(f"Search path does not exist: {path}")

        file_paths = []

        def find_files_recursive(current_path: Path, current_depth: int = 0):
            # Check depth limit
            if max_depth is not None and current_depth > max_depth:
                return
            
            try:
                # Walk current directory
                for item in current_path.iterdir():
                    # Skip hidden files/directories if required
                    if not search_hidden and item.name.startswith('.'):
                        continue
                    
                    # Exclude .git directory
                    if item.name == '.git' and item.is_dir():
                        continue
                    
                    # Handle symbolic links
                    if item.is_symlink():
                        if not follow_symlinks:
                            continue
                        try:
                            item = item.resolve()
                        except (OSError, RuntimeError):
                            continue
                    
                    if item.is_file():
                        # Check whether it matches the include patterns
                        if self._should_include_file(item, include_patterns):
                            file_paths.append(str(item))
                    elif item.is_dir():
                        find_files_recursive(item, current_depth + 1)
            
            except (PermissionError, OSError) as e:
                logger.debug(f"Cannot access directory {current_path}: {e}")

        # Run file discovery in a thread pool
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, find_files_recursive, search_path, 0)

        logger.debug(f"Pygrep file discovery finished: found {len(file_paths)} files")
        return file_paths