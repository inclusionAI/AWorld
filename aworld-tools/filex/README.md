# FileX for AWorld

This directory contains the complete FileX runtime used by the AWorld `filex` skill. It is self-contained inside the AWorld repository and includes the CLI, document/media parsing services, required utility and AFTS modules, dependency lock, container recipe, examples, and FileX tests.

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
docker build -t aworld-filex:local aworld-tools/filex
docker run --rm aworld-filex:local filex --help
```

Override `BASE_IMAGE` when layering FileX onto an AWorld sandbox base image. The Dockerfile deliberately does not replace an inherited entrypoint or command.

The repository also includes `.github/workflows/filex-image.yml`. Pull requests
build the image for validation, while pushes to `main`, `codex/**`, and tags
matching `filex-v*` publish the Linux AMD64 image to
`ghcr.io/inclusionai/aworld-filex`. GitHub's built-in `GITHUB_TOKEN` is used, so
the workflow does not require a registry password or an additional secret.

## Source provenance

The initial source was imported from the local `mcp_servers/leopard-mcp-server/mcp_servers/filesystem_server` FileX implementation at commit `f32b51c0266a8d1104450ebd59f76680f4312a5a`. The credential-bearing source `config/filex.yaml` was not imported.
