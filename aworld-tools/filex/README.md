# FileX for AWorld

This directory contains the complete FileX runtime used by the AWorld `filex` skill. It is self-contained inside the AWorld repository and includes the CLI, document/media parsing services, required utility and AFTS modules, dependency lock, container recipe, examples, and FileX tests. The published container also installs AWorld and `aworld-cli`, making it a single ready-to-run image.

Supported inputs include PDF, text/Markdown, Word, PowerPoint, Excel/CSV, images, audio, and video. The CLI exposes `filex save`, `filex parse`, and `filex status`.

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
  aworld-cli --agent Aworld --skill filex \
  --task "Parse @report.pdf and summarize it"
```

For a FileX provider that needs structured configuration, copy
`config/filex-env.example.json` to `workspace/filex-env.json`, restrict its
permissions, and tell the FileX skill to use
`--env-file /root/workspace/filex-env.json`. Native text, Office, table, and
LiteParse flows do not require provider credentials. AFTS, remote VLM, and
remote media providers require only the relevant fields; unused empty fields
should be removed.

The repository also includes `.github/workflows/filex-image.yml`. Pull requests
build the all-in-one image for validation, while pushes to `main`, `codex/**`, and tags
matching `filex-v*` publish the Linux AMD64 image to
`ghcr.io/inclusionai/aworld-filex`. GitHub's built-in `GITHUB_TOKEN` is used, so
the workflow does not require a registry password or an additional secret.

## Source provenance

The initial source was imported from the local `mcp_servers/leopard-mcp-server/mcp_servers/filesystem_server` FileX implementation at commit `f32b51c0266a8d1104450ebd59f76680f4312a5a`. The credential-bearing source `config/filex.yaml` was not imported.
