---
name: mcp-filex
description: 在 MCP 环境中使用 filex CLI 解析、保存和调试文件的中文 use case 手册，适用于通过 bash 工具执行 filex 命令并查看解析结果。
---

# MCP 中使用 filex CLI

这份说明用于记录在 MCP 环境里如何使用 `filex` CLI。重点是 CLI 的实际用法和排查方式，不包含沙箱创建流程。

## 基本原则

- 通过 MCP 的 bash/terminal 工具执行命令。
- 在 MCP 容器内进入 filesystem server 目录后运行 `./bin/filex`。
- 不要用宿主机本地 `.venv` 的结果判断 MCP 镜像是否可用，除非明确是在做本地验证。
- 当前线上镜像的 filex 工作目录通常是 `/root/fs_workspace`；后续 workspace 默认目录改造部署后会切到 `/root/workspace`。

```bash
cd /app/mcp_servers/filesystem_server
./bin/filex --help
```

## Use Case 1：解析 workspace 内文件

适用于文件已经在 MCP 容器的 workspace 目录下，例如 Excel、PDF、图片、音视频等。

```bash
cd /app/mcp_servers/filesystem_server

./bin/filex parse \
  --workspace-path /root/fs_workspace/input.xlsx \
  --file-type xlsx \
  --sync-mode sync \
  --asset-reference-mode remote_id
```

常见 `file-type`：

- `pdf`
- `docx`
- `pptx`
- `xlsx`
- `csv`
- `txt`
- `jpg` / `jpeg` / `png`
- `mp3` / `wav`
- `mp4` / `mov`

## Use Case 2：通过 AFTS fileId 解析文件

适用于文件已经上传到 AFTS，只知道 `fileId` 的情况。

```bash
cd /app/mcp_servers/filesystem_server

./bin/filex parse \
  --file-id 'A*JbQOSIxgUv4AAAAAQlAAAAgAegAAAQ' \
  --file-type docx \
  --sync-mode sync \
  --asset-reference-mode remote_id
```

`remote_id` 模式会尽量把解析产物或图片资源上传/保留为远端引用，适合后续在 markdown 中保留可访问的资源信息。

## Use Case 3：保存 AFTS 文件到 workspace

如果想先把远端文件下载到 workspace，再用 `--workspace-path` 解析，可以先执行 `save`。

```bash
cd /app/mcp_servers/filesystem_server

./bin/filex save \
  --file-id 'A*JbQOSIxgUv4AAAAAQlAAAAgAegAAAQ' \
  --output /root/fs_workspace/input.docx
```

然后再解析：

```bash
./bin/filex parse \
  --workspace-path /root/fs_workspace/input.docx \
  --file-type docx \
  --sync-mode sync \
  --asset-reference-mode remote_id
```

## Use Case 4：Excel smoke case

用于确认 MCP 镜像里的 Excel 解析依赖是否完整。之前验证过 MCP 环境中会进入 `ExcelDocumentService`，并完成 `content_extract`。

```bash
cd /app/mcp_servers/filesystem_server
mkdir -p /root/fs_workspace

# 准备一个 xlsx 后执行：
./bin/filex parse \
  --workspace-path /root/fs_workspace/filex-excel-case.xlsx \
  --file-type xlsx \
  --sync-mode sync \
  --asset-reference-mode remote_id
```

成功时日志通常包含：

- `service_type=ExcelDocumentService`
- `content_extract completed`
- `sync_parse success`
- `output_file_id=...`

## Use Case 5：查看解析结果

`filex parse` 输出 JSON。成功时通常会写出 markdown 文件，并可能上传到 AFTS。

本地文件路径一般在日志里：

```bash
/root/fs_workspace/document_parse/<task_id>/<source_name>.md
```

可以在同一个 bash 里直接查看：

```bash
cat /root/fs_workspace/document_parse/<task_id>/<source_name>.md
```

如果 CLI JSON 里没有内联 markdown，不代表解析失败；以日志中的 `parsed_file_path`、`output_file_id` 和 `sync_parse success` 为准。

## Use Case 6：复杂 bash 脚本

如果通过 MCP bash 工具传入多行脚本，脚本里包含 heredoc、JSON、Python 或 `$` 变量，建议先 base64 后再执行，避免命令转义破坏。

```bash
printf %s '<base64-script>' | base64 -d > /tmp/filex_case.sh
bash /tmp/filex_case.sh
```

如果看到类似 `here-document ... wanted INNERnfrom` 的错误，通常就是命令字符串被转义损坏了。

## Use Case 7：批量验证本地目录

用于把宿主机上的一批样例文件通过 MCP/filex 链路逐个解析，适合回归 `md/csv/docx/xlsx/pptx/pdf` 这类混合目录。

```bash
examples/document_parse/skills/mcp-filex/scripts/run_mcp_filex_directory_case.sh \
  --source-dir /Users/wuhulala/Downloads/shanyingyong \
  --token "$MCP_TOKEN"
```

脚本行为：

- 宿主机只负责读取文件和驱动 MCP，不在本地执行 filex。
- 每个文件单独调用一次 MCP `terminal-server__execute_command`。
- 单次命令内完成 `base64 decode -> 写入 /root/fs_workspace -> ./bin/filex parse`。
- PDF 默认走 `--env-content-json '{"pdf_parse_provider":"paddle_ocr"}'`。
- 输出每个文件的 `success`、耗时、`output_file_id`、`file_url`、`file_path` 和失败原因。

这类 remote terminal 调用不要依赖多次命令之间的 `/tmp` 或 workspace 文件持久化；之前验证过分片写入再解包会因为跨调用文件不可见而失败。也尽量不要传多行 heredoc，remote command 里换行可能被转成字面量 `n`。

当前单命令写入方式有命令长度上限：约 100KB 的 PDF base64 后可能触发 `[Errno 7] Argument list too long: '/bin/bash'`。这类文件优先走 AFTS fileId，再用 `filex parse --file-id ...`。

最近一次 `/Users/wuhulala/Downloads/shanyingyong` 回归结果：

- 总计 25 个文件，成功 19 个。
- `md/csv/docx/xlsx` 全部成功。
- 5 个较小 PDF 成功，约 30-33 秒/个。
- 2 个较大 PDF 失败，原因是单命令参数过长。
- 4 个 PPTX 失败，原因是 `LibreOffice conversion succeeded but output PDF not found`。

## 常见问题

- `path must be under the filesystem workspace`：输入文件不在允许的 workspace 目录下。当前镜像优先放到 `/root/fs_workspace`。
- `未安装 pandas`：如果发生在宿主机本地，不代表 MCP 镜像缺依赖；需要在 MCP 中复测。
- `VIRTUAL_ENV=... does not match ...`：terminal server 调 filesystem server 时常见 warning，通常不是解析失败原因。
- CLI 成功但 `markdown_len=0`：可能是返回 JSON 没内联 markdown。看日志里的 `parsed_file_path` 或 `output_file_id`。
- `[Errno 7] Argument list too long`：通常是把大文件 base64 直接塞进单条 bash 命令。改用 AFTS fileId 或缩小单条命令。
- `LibreOffice conversion succeeded but output PDF not found`：PPTX/Office 转换阶段失败，已经进入对应 DocumentService，但 LibreOffice 输出路径没有产物，需要查转换输出目录和文件名匹配逻辑。

## 可复用脚本

Excel smoke case 脚本：

```bash
examples/document_parse/skills/mcp-filex/scripts/run_mcp_filex_excel_case.sh
```

这个脚本会通过 MCP bash 工具在容器里创建一个小 Excel，并调用 `filex parse` 验证 Excel 解析链路。

目录批量 case 脚本：

```bash
examples/document_parse/skills/mcp-filex/scripts/run_mcp_filex_directory_case.sh
```

这个脚本用于把本地目录中的多类型文件逐个送入 MCP/filex 链路，适合做批量 smoke test 和格式兼容性回归。
