# Filesystem MCP Server

Filesystem MCP server implemented with FastMCP. It exposes a rich set of file and directory operations over the MCP protocol.

## Features

- Read and write files  
- Create / list / delete directories  
- Move / rename files  
- Search files  
- Get file metadata  
- Dynamic allowed-directories access control  

## Installation

Using `uv` (recommended):

```bash
uv pip install -e .
```

Or using `pip`:

```bash
pip install -e .
```

## Usage

### Run from command line

```bash
# Using uv
uv run python -m src [ALLOWED_DIR] [OTHER_DIR ...]

# Or using plain Python / pip env
python -m src [ALLOWED_DIR] [OTHER_DIR ...]
```

Each argument is treated as an allowed root directory; all file operations must stay under at least one of them.

### MCP client configuration

Add to your MCP client config (for example `~/.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "uv",
      "args": [
        "run",
        "python",
        "-m",
        "src",
        "/path/to/allowed/directory"
      ]
    }
  }
}
```

## Tools

The server exposes the following tools (exact signatures are defined in `src/main.py`):

### `read_text_file`
Read a text file.

- Supports `head` parameter: read only the first N lines  
- Supports `tail` parameter: read only the last N lines  
- `head` and `tail` cannot be used at the same time  

### `read_media_file`
Read an image or audio file.

- Returns base64-encoded data and MIME type  
- Supported image formats: PNG, JPG, GIF, WebP, BMP, SVG  
- Supported audio formats: MP3, WAV, OGG, FLAC  

### `read_multiple_files`
Read multiple files in one call.

- Reads several files concurrently for better performance  
- Failure on one file does not affect the others  

### `write_file`
Create or overwrite a file.

- Completely overwrites the existing file content  
- Automatically creates parent directories  

### `edit_file`
Edit file content.

- Line-based or range-based edits (see tool schema for exact parameters)  
- Supports `dryRun` mode: preview the change without applying it  
- Returns a git-style diff preview of the changes  

### `create_directory`
Create a directory.

- Automatically creates parent directories (recursive)  
- Succeeds silently if the directory already exists  

### `list_directory`
List the contents of a directory.

- Shows both files and subdirectories  
- Uses `[FILE]` and `[DIR]` prefixes to distinguish types  

### `list_directory_with_sizes`
List directory contents with size information.

- Shows file sizes  
- Supports sorting by name or size  
- Returns aggregate statistics (total files, total directories, total size)  

### `directory_tree`
Get a recursive directory tree.

- Returns a JSON representation of the directory tree  
- Supports exclude patterns (glob)  
- Each node includes name, type, and children  

### `move_file`
Move or rename a file.

- Can move files between directories  
- Can rename files within the same directory  
- Fails if the destination path already exists  

### `search_files`
Search for files.

- Uses glob patterns for matching  
- Recursively searches subdirectories  
- Supports exclude patterns  
- Returns a list of full paths for all matches  

### `get_file_info`
Get file metadata.

- File size  
- Creation / modification / access times  
- File type (file / directory)  
- Permission info  

### `list_allowed_directories`
List all allowed directories.

- Shows all directories that the server is currently configured to allow  
- Useful to understand the accessible scope  

# Filesystem MCP Server

基于 FastMCP 实现的文件系统 MCP 服务器。

## 功能特性

- 读写文件
- 创建/列出/删除目录
- 移动/重命名文件
- 搜索文件
- 获取文件元数据
- 动态目录访问控制

## 安装

使用 `uv`（推荐）：

```bash
uv pip install -e .
```

或使用 `pip`：

```bash
pip install -e .
```

## 使用方法

### 命令行运行

```bash
# 使用 uv
uv run python -m src [允许的目录] [其他目录...]

# 或使用 pip
python -m src [允许的目录] [其他目录...]
```

### MCP 客户端配置

添加到 MCP 客户端配置文件（如 `~/.cursor/mcp.json`）：

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "uv",
      "args": [
        "run",
        "python",
        "-m",
        "src",
        "/path/to/allowed/directory"
      ]
    }
  }
}
```

## 工具列表

服务器提供以下工具：

### `read_text_file`
读取文本文件内容
- 支持 `head` 参数：只读取前 N 行
- 支持 `tail` 参数：只读取后 N 行
- 不能同时指定 `head` 和 `tail`

### `read_media_file`
读取图片或音频文件
- 返回 base64 编码的数据和 MIME 类型
- 支持图片格式：PNG, JPG, GIF, WebP, BMP, SVG
- 支持音频格式：MP3, WAV, OGG, FLAC

### `read_multiple_files`
批量读取多个文件
- 同时读取多个文件，提高效率
- 单个文件读取失败不会影响其他文件

### `write_file`
创建或覆盖文件
- 完全覆盖现有文件内容
- 自动创建父目录

### `edit_file`
编辑文件内容
- 支持文本查找替换
- 支持 `dryRun` 模式：预览修改而不实际应用
- 返回 git-style diff 格式的变更预览
- 自动保留缩进格式

### `create_directory`
创建目录
- 自动创建父目录（递归）
- 目录已存在时静默成功

### `list_directory`
列出目录内容
- 显示文件和目录列表
- 使用 `[FILE]` 和 `[DIR]` 前缀区分类型

### `list_directory_with_sizes`
列出目录内容（带大小信息）
- 显示文件大小
- 支持按名称或大小排序
- 显示统计信息（总文件数、总目录数、总大小）

### `directory_tree`
获取目录树结构
- 返回 JSON 格式的递归目录树
- 支持排除模式（glob 格式）
- 每个节点包含名称、类型和子节点

### `move_file`
移动或重命名文件
- 可以移动文件到不同目录
- 可以在同一目录内重命名
- 目标路径已存在时会失败

### `search_files`
搜索文件
- 使用 glob 模式匹配
- 递归搜索子目录
- 支持排除模式
- 返回匹配文件的完整路径列表

### `get_file_info`
获取文件元数据
- 文件大小
- 创建时间、修改时间、访问时间
- 文件类型（文件/目录）
- 权限信息

### `list_allowed_directories`
列出允许访问的目录
- 显示服务器当前允许访问的所有目录
- 用于了解可访问范围

