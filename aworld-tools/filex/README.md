# FileX for AWorld

This directory contains the FileX runtime used by the AWorld `filex` skill. It is self-contained inside the AWorld repository and includes the CLI, local path/URL document and media parsing services, required utilities, container recipe, examples, and FileX tests. The published container also installs AWorld and `aworld-cli`, making it a single ready-to-run image.

Supported inputs include PDF, text/Markdown, Word, PowerPoint, Excel/CSV, images, audio, and video. Sources can be workspace paths, HTTP(S) file URLs, or supported source-provider URLs such as YouTube. The CLI exposes `filex inspect`, `filex parse`, and `filex status`; it does not require a file service or file ID.

## 本地使用（推荐 Docker）

### 1. 准备环境和工作目录

本地需要 Docker Desktop、Colima 或其他兼容 Docker 的运行时。macOS、Linux
和 Windows WSL2 均可使用。所有本地输入、输出和配置文件都应放在同一个
工作目录中；容器会将其挂载为 `/root/workspace`。

```bash
mkdir -p workspace
cp /path/to/report.pdf workspace/report.pdf
```

从 GHCR 拉取已经发布的镜像：

```bash
docker pull --platform linux/amd64 ghcr.io/inclusionai/aworld-filex:latest
export FILEX_IMAGE=ghcr.io/inclusionai/aworld-filex:latest
```

如果镜像尚未发布，进入 AWorld 仓库根目录后本地构建：

```bash
docker buildx build --load \
  --platform linux/amd64 \
  -f aworld-tools/filex/Dockerfile \
  -t aworld-filex:local .
export FILEX_IMAGE=aworld-filex:local
```

当前镜像发布为 Linux AMD64；Apple Silicon 本地运行时保留
`--platform linux/amd64` 即可。

### 2. 解析本地文件

宿主机的 `workspace/report.pdf` 在容器内对应
`/root/workspace/report.pdf`：

```bash
docker run --rm \
  --platform linux/amd64 \
  -v "$PWD/workspace:/root/workspace" \
  "$FILEX_IMAGE" \
  filex parse \
    --workspace-path /root/workspace/report.pdf \
    --asset-reference-mode local_path
```

命令向标准输出返回 JSON。`success` 为 `true` 时，`file_path` 是生成的
Markdown 路径；生成文件和图片资源都会保留在宿主机的 `workspace` 目录中。

也可以使用随镜像安装的 FileX Skill 包装脚本，并明确指定输出文件：

```bash
docker run --rm \
  --platform linux/amd64 \
  -v "$PWD/workspace:/root/workspace" \
  "$FILEX_IMAGE" \
  python3 /skills/filex/scripts/filex.py parse \
    --input /root/workspace/report.pdf \
    --output /root/workspace/report.md
```

### 3. 解析 URL

FileX 可直接接收 HTTP(S) URL。文件会先下载到受限的容器工作区，再进入与
本地 path 相同的解析流程，不需要 `fileId`：

```bash
docker run --rm \
  --platform linux/amd64 \
  -v "$PWD/workspace:/root/workspace" \
  "$FILEX_IMAGE" \
  filex parse \
    --url "https://example.com/report.pdf" \
    --file-type pdf
```

默认下载超时为 120 秒，最大文件为 512 MiB。可在运行容器时覆盖：

```bash
docker run --rm \
  --platform linux/amd64 \
  -e FILEX_DOWNLOAD_TIMEOUT_SECONDS=300 \
  -e FILEX_MAX_DOWNLOAD_BYTES=1073741824 \
  -v "$PWD/workspace:/root/workspace" \
  "$FILEX_IMAGE" \
  filex parse --url "https://example.com/large.pdf" --file-type pdf
```

新调用方式也可把路径或 URL 作为位置参数；旧的 `--url` 与
`--workspace-path` 参数继续兼容：

```bash
filex parse /root/workspace/report.pdf
filex parse "https://example.com/report.pdf" --file-type pdf
```

#### YouTube transcript-first

`inspect` 只发现元数据、章节、字幕和发布者 transcript 候选，不下载音视频：

```bash
filex inspect "https://www.youtube.com/watch?v=3i7ym_Qh7BA"
```

解析时默认优先人工字幕，其次自动字幕，并生成带时间戳的 Markdown、
`source.json` 和来源指标：

```bash
filex parse \
  "https://www.youtube.com/watch?v=3i7ym_Qh7BA" \
  --mode transcript \
  --language en
```

本阶段会在 `source.json` 中登记视频简介发现的发布者 transcript URL，但不自动
抓取任意外站 HTML。只有字幕不可用、用户明确允许且声明权利基础时，才下载
音轨并交给本地 Whisper：

```bash
filex parse \
  "https://www.youtube.com/watch?v=VIDEO_ID" \
  --mode transcript \
  --allow-media-download \
  --rights-basis user-owned
```

`--rights-basis` 支持 `user-owned`、`licensed`、`service-permitted` 和
`applicable-law`。FileX 不会自动使用浏览器 Cookie，也不提供绕过 DRM、地区、
年龄或其他访问控制的能力。当前 YouTube provider 聚焦 transcript；镜头切分、
关键帧 OCR 和完整声画理解将在独立视频 provider 中实现。

Source discovery 默认单次网络超时为 20 秒、重试 1 次，可通过
`FILEX_SOURCE_TIMEOUT_SECONDS` 和 `FILEX_SOURCE_RETRIES` 调整。字幕单文件上限
默认 32 MiB，可通过 `FILEX_MAX_SUBTITLE_BYTES` 调整。

### 4. 使用 aworld-cli 和 FileX Skill

仅做原生 PDF、Office、文本或表格解析时，可以直接使用前面的 `filex`
命令，不需要模型 key。要让 AWorld Agent 自动调用 FileX 并继续总结、问答，
需要配置 AWorld 使用的模型：

```bash
cp aworld-tools/filex/config/aworld.env.example .env.aworld-filex
chmod 600 .env.aworld-filex
```

编辑 `.env.aworld-filex`，填写 `LLM_PROVIDER`、`LLM_MODEL_NAME`、
`LLM_BASE_URL` 和 `LLM_API_KEY`，然后运行：

```bash
docker run --rm -it \
  --platform linux/amd64 \
  --env-file .env.aworld-filex \
  -v "$PWD/workspace:/root/workspace" \
  "$FILEX_IMAGE" \
  aworld-cli run \
    --agent Aworld \
    --skill filex \
    --task "解析 /root/workspace/report.pdf，并总结主要内容"
```

进入交互模式：

```bash
docker run --rm -it \
  --platform linux/amd64 \
  --env-file .env.aworld-filex \
  -v "$PWD/workspace:/root/workspace" \
  "$FILEX_IMAGE" aworld-cli
```

### 5. FileX provider 配置

镜像内的 `/opt/filex/config/filex.yaml` 是无密钥默认策略，负责 provider、
PDF 分批大小和本地音视频模型等全局设置。一般无需修改。

需要远程 VLM 或其他带认证的 provider 时，将示例配置复制到挂载目录：

```bash
cp aworld-tools/filex/config/filex-env.example.json workspace/filex-env.json
chmod 600 workspace/filex-env.json
```

填写需要的 `base_url`、`model_name` 和 `api_key` 后运行：

```bash
docker run --rm \
  --platform linux/amd64 \
  -v "$PWD/workspace:/root/workspace" \
  "$FILEX_IMAGE" \
  filex parse \
    --workspace-path /root/workspace/report.pdf \
    --env-content-file /root/workspace/filex-env.json
```

不要把真实 key 写入 Dockerfile、`filex.yaml`、Git 仓库或命令行参数。

### 6. 常用检查和排障

```bash
# 确认镜像中的两个 CLI 都可用
docker run --rm --platform linux/amd64 "$FILEX_IMAGE" filex --help
docker run --rm --platform linux/amd64 "$FILEX_IMAGE" aworld-cli --help

# 查看工作区产物
find workspace -maxdepth 4 -type f
```

- `path must be under the filesystem workspace`：输入文件没有放在挂载的
  `workspace` 目录中，或传入了宿主机路径而不是容器路径。
- `File does not exist`：检查 `-v "$PWD/workspace:/root/workspace"` 和文件名。
- 模型认证失败：检查 `.env.aworld-filex` 或 `workspace/filex-env.json`，不要
  把 AWorld Agent 的模型配置和 FileX provider 配置混用。
- Apple Silicon 出现架构错误：增加 `--platform linux/amd64`。
- 解析大型 PDF：使用 `--pages 1,3-5`、`--page-batch-size 10` 和
  `--batch-resume-id <id>` 控制页范围、分批和断点续跑。

## Local development

```bash
uv sync --dev
uv run filex --help
uv run pytest tests/document_parse_service
```

FileX writes under `~/workspace` by default. Runtime provider configuration can be supplied with `--env-content-file`; credentials must not be committed.

## Container

```bash
docker build -f aworld-tools/filex/Dockerfile -t aworld-filex:local .
mkdir -p workspace
docker run --rm -it \
  --platform linux/amd64 \
  -v "$PWD/workspace:/root/workspace" \
  aworld-filex:local
```

The default command is `aworld-cli`. Override it to call FileX directly:

```bash
docker run --rm \
  --platform linux/amd64 \
  -v "$PWD/workspace:/root/workspace" \
  aworld-filex:local \
  filex parse --workspace-path /root/workspace/report.pdf --sync-mode sync
```

The image contains AWorld, `aworld-cli`, all repository skills, FileX, and the
system parsing dependencies. Only `/root/workspace` and runtime model/provider
configuration need to be supplied. Credentials and model weights are not baked
into the image.

## Runtime configuration

Copy the AWorld environment template outside the repository and fill in the
model settings:

```bash
cp aworld-tools/filex/config/aworld.env.example .env.aworld-filex
chmod 600 .env.aworld-filex
```

Pass it to the container at runtime:

```bash
docker run --rm -it \
  --platform linux/amd64 \
  --env-file .env.aworld-filex \
  -v "$PWD/workspace:/root/workspace" \
  ghcr.io/inclusionai/aworld-filex:latest \
  aworld-cli run --agent Aworld --skill filex \
  --task "Parse @report.pdf and summarize it"
```

For a FileX provider that needs structured configuration, copy
`config/filex-env.example.json` to `workspace/filex-env.json`, restrict its
permissions, and tell the FileX skill to use
`--env-file /root/workspace/filex-env.json`. Native text, Office, table, and
LiteParse flows do not require provider credentials. Remote VLM and remote
media providers require only the relevant fields; unused empty fields
should be removed.

The image includes a credential-free `/opt/filex/config/filex.yaml` with native
defaults (LiteParse PDF, python-pptx, 10-page PDF batches, and local
audio/video transcription). To replace these defaults, mount another YAML file
read-only and set `FILEX_CONFIG_PATH`:

```bash
docker run --rm -it \
  -e FILEX_CONFIG_PATH=/root/workspace/filex.yaml \
  -v "$PWD/workspace:/root/workspace" \
  ghcr.io/inclusionai/aworld-filex:latest \
  filex parse --workspace-path /root/workspace/report.pdf
```

Keep secrets out of `filex.yaml`; pass provider credentials with the mounted
`filex-env.json` and `--env-content-file` instead.

The repository also includes `.github/workflows/filex-image.yml`. Pull requests
build the all-in-one image for validation, while pushes to `main`, `codex/**`, and tags
matching `filex-v*` publish the Linux AMD64 image to
`ghcr.io/inclusionai/aworld-filex`. GitHub's built-in `GITHUB_TOKEN` is used, so
the workflow does not require a registry password or an additional secret.

## Source provenance

The initial source was imported from the local `mcp_servers/leopard-mcp-server/mcp_servers/filesystem_server` FileX implementation at commit `f32b51c0266a8d1104450ebd59f76680f4312a5a`. Its credential-bearing configuration was not imported; the bundled `config/filex.yaml` is a new credential-free default.
