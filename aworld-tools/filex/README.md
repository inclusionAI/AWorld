# FileX for AWorld

FileX is AWorld's document and media ingestion runtime. It turns local files,
public URLs, or restricted object-store references into Markdown and structured
artifacts that an agent can inspect without copying a large source file through
the model context.

The repository ships two deployment shapes:

- **AWorld all-in-one image**: `aworld-cli`, the FileX skill, and the FileX CLI.
- **FileX GPU service**: an asynchronous HTTP service with a bounded queue and a
  persistent PaddleOCR-VL worker for scanned documents.

Supported inputs include PDF, Markdown/text, Word, PowerPoint, Excel/CSV,
images, audio, video, HTTP(S) files, and YouTube transcript sources. PDF output
uses Document IR v3; video output can include timestamped keyframes, OCR
evidence, and a storyboard. Video evidence is not yet a full semantic video
understanding model.

## Quick start

### Local development

FileX requires Python 3.12, `uv`, FFmpeg, LibreOffice, Poppler, and Noto CJK
fonts. From this directory:

```bash
cd aworld-tools/filex
uv sync --dev
uv run filex --help
uv run filex parse /path/to/report.pdf
uv run pytest tests/document_parse_service
```

FileX writes under `~/workspace` by default. Use `FILEX_WORKSPACE_ROOT` to select
another workspace. Inputs must remain inside that workspace when using
`--workspace-path`.

### AWorld all-in-one container

Build from the AWorld repository root:

```bash
docker build --platform linux/amd64 \
  -f aworld-tools/filex/Dockerfile -t aworld-filex:local .
mkdir -p workspace
cp /path/to/report.pdf workspace/report.pdf
docker run --rm \
  --platform linux/amd64 \
  -v "$PWD/workspace:/root/workspace" \
  aworld-filex:local \
  filex parse /root/workspace/report.pdf
```

The default container command is `aworld-cli`. The image also exposes FileX
through the bundled skill:

```bash
docker run --rm -it \
  --platform linux/amd64 \
  --env-file .env.aworld-filex \
  -v "$PWD/workspace:/root/workspace" \
  aworld-filex:local \
  aworld-cli run --agent Aworld --skill filex \
  --task "Parse /root/workspace/report.pdf and summarize it"
```

From the AWorld repository root, create the runtime environment file and keep
real model credentials outside the repository:

```bash
cp aworld-tools/filex/config/aworld.env.example .env.aworld-filex
chmod 600 .env.aworld-filex
```

## File and URL parsing

The CLI accepts a workspace path or an HTTP(S) URL:

```bash
filex inspect /root/workspace/report.pdf
filex parse /root/workspace/report.pdf
filex parse "https://example.com/report.pdf" --file-type pdf
filex status --batch-resume-id <resume-id>
```

For large PDFs, select pages and enable resumable batches:

```bash
filex parse /root/workspace/report.pdf \
  --pages 1,3-20 \
  --page-batch-size 3 \
  --batch-resume-id report-2026-01
```

YouTube sources use a transcript-first policy. Discovery does not download
media; audio fallback requires explicit permission and a rights basis:

```bash
filex inspect "https://www.youtube.com/watch?v=VIDEO_ID"
filex parse "https://www.youtube.com/watch?v=VIDEO_ID" \
  --mode transcript --language en
filex parse "https://www.youtube.com/watch?v=VIDEO_ID" \
  --mode transcript --allow-media-download --rights-basis user-owned
```

FileX does not use browser cookies or bypass access controls.

## Asynchronous HTTP service

Run the service from the all-in-one image:

```bash
docker run --rm \
  --platform linux/amd64 \
  -p 18080:18080 \
  -e FILEX_SERVICE_HOST=0.0.0.0 \
  -e FILEX_SERVICE_CONCURRENCY=1 \
  -e FILEX_SERVICE_MAX_PENDING_JOBS=8 \
  -v "$PWD/workspace:/root/workspace" \
  aworld-filex:local filex-server
```

Submit a file without authentication when no service token is configured:

```bash
curl -sS -X POST http://127.0.0.1:18080/v1/parse \
  -F 'file=@report.pdf' \
  -F 'provider=liteparse'
```

Submit a public URL so FileX downloads the source directly:

```bash
curl -sS -X POST http://127.0.0.1:18080/v1/parse \
  -F 'source_url=https://example.com/report.pdf' \
  -F 'provider=liteparse'
```

For a private object store, set `FILEX_SERVICE_SOURCE_URL_HOSTS` to a
comma-separated allowlist and submit a short-lived pre-signed URL together with
its expected size and SHA-256. FileX validates redirects, destination addresses,
size, and digest. Public URL mode rejects private and loopback destinations.

Poll `GET /v1/jobs/{job_id}`, cancel with
`DELETE /v1/jobs/{job_id}`, and download successful artifacts through the URLs
returned by the job response. `GET /healthz` reports queue and worker state.

### GPU service image

The GPU overlay expects a revision-pinned Faster Whisper model directory at
`.ci-models/faster-whisper-base`. From the FileX directory, prepare it and build
the overlay:

```bash
./bin/download-whisper-model.sh .ci-models/faster-whisper-base
docker build --platform linux/amd64 \
  -f Dockerfile.gpu-service -t filex-gpu:local .
docker run --rm --gpus all \
  --platform linux/amd64 \
  --shm-size=8g \
  -p 18080:18080 \
  -e FILEX_SERVICE_HOST=0.0.0.0 \
  -e FILEX_SERVICE_PADDLE_WARMUP=true \
  -e FILEX_SERVICE_PADDLE_IDLE_SECONDS=0 \
  -v filex-paddlex-cache:/root/.paddlex \
  -v "$PWD/workspace:/root/workspace" \
  filex-gpu:local
```

The default GPU overlay is pinned to a published Linux AMD64 AWorld base image.
To test the overlay against a local base, pass
`--build-arg BASE_IMAGE=aworld-filex:local` and make that image available to the
builder.

PaddleOCR-VL model weights are downloaded during the first warmup unless they
already exist in the image or `/root/.paddlex` cache. The first start therefore
needs model-registry network access. Persist the cache volume as shown above,
or pre-warm and capture the cache during an image build for an offline runtime.
After the service becomes ready, submit scanned PDFs with:

```bash
curl -sS -X POST http://127.0.0.1:18080/v1/parse \
  -F 'file=@scanned-report.pdf' \
  -F 'provider=paddle_ocr'
```

`FILEX_SERVICE_PADDLE_IDLE_SECONDS=0` keeps the OCR worker resident. A positive
value unloads it only after that many idle seconds; active or queued work does
not trigger idle shutdown. The no-progress watchdog restarts a stalled worker.

## Hardware profiles

| Profile | Suitable workload | Requirements |
| --- | --- | --- |
| CPU development | Text, Office, tables, text-layer PDFs, light audio/video | 4+ CPU cores, 16 GiB RAM recommended |
| GPU OCR service | Scanned PDFs and sustained PaddleOCR-VL traffic | NVIDIA GPU with 16 GiB+ VRAM recommended, 32 GiB RAM, 8 GiB shared memory |
| Validated service host | Long-running OCR service validation | NVIDIA RTX 5090 32 GiB; about 8.5 GiB resident VRAM was observed after warmup, not measured as peak or minimum |

The GPU image installs PaddlePaddle GPU 3.3.0 from the CUDA 12.9 package index
by default. The host needs a compatible NVIDIA driver and NVIDIA Container
Toolkit. Reserve additional disk space for container layers, model weights, and
job artifacts; tens of GiB is a practical starting point. CPU-only scanned-PDF
OCR is not recommended as a production throughput profile.

## Configuration

### Service environment

| Variable | Default | Purpose |
| --- | --- | --- |
| `FILEX_SERVICE_HOST` | `127.0.0.1` | Listen address |
| `FILEX_SERVICE_PORT` | `18080` | Listen port |
| `FILEX_SERVICE_CONCURRENCY` | `1` | Maximum simultaneous parses |
| `FILEX_SERVICE_MAX_PENDING_JOBS` | `8` | Queue bound; excess requests receive backpressure |
| `FILEX_SERVICE_MAX_UPLOAD_BYTES` | `1073741824` | Upload and remote-source size limit |
| `FILEX_SERVICE_PARSE_TIMEOUT_SECONDS` | `1800` | Per-job deadline |
| `FILEX_SERVICE_SOURCE_URL_TIMEOUT_SECONDS` | `900` | Remote-source download deadline |
| `FILEX_SERVICE_SOURCE_URL_HOSTS` | empty | Allowed private object-store hosts |
| `FILEX_SERVICE_API_TOKEN` | empty | Optional bearer token |
| `FILEX_SERVICE_API_TOKEN_FILE` | empty | Optional bearer-token file |
| `FILEX_SERVICE_TENANT_ID` | empty | Optional required value for the `X-Tenant-ID` header |
| `FILEX_SERVICE_PADDLE_WARMUP` | `false` | Warm the OCR worker on service startup |
| `FILEX_SERVICE_PADDLE_IDLE_SECONDS` | `0` | Idle unload delay; `0` keeps the model resident |
| `FILEX_SERVICE_PADDLE_NO_PROGRESS_SECONDS` | `300` | Stalled-worker watchdog |
| `FILEX_SERVICE_LOG_LEVEL` | `INFO` | Service log level |

Set `FILEX_SERVICE_CONCURRENCY=1` for one-GPU PaddleOCR-VL deployments. The
queue is deliberately bounded so callers receive backpressure instead of
unbounded memory and disk growth.

### Parser and model configuration

`config/filex.yaml` contains credential-free provider defaults. Replace it by
mounting a YAML file and setting `FILEX_CONFIG_PATH`. The CLI can load provider
credentials with `--env-content-file` using a copy of
`config/filex-env.example.json`; the HTTP endpoint does not accept arbitrary
provider credentials per request. Configure service-side providers at startup.
Do not put secrets in the image, YAML, command history, or Git.

Useful model variables are:

- `FILEX_LOCAL_MEDIA_MODEL`
- `FILEX_LOCAL_MEDIA_DEVICE`
- `FILEX_LOCAL_MEDIA_COMPUTE_TYPE`
- `FILEX_CONFIG_PATH`
- `FILEX_WORKSPACE_ROOT`

## Operational model

- HTTP jobs are persisted under the workspace and survive client disconnects.
- FileX uses one persistent Paddle worker, three-page OCR batches, checkpoints,
  and per-batch progress so long PDFs can resume without recomputing completed
  pages.
- The service downloads pre-signed URLs directly, avoiding an extra large-file
  copy through an agent runtime.
- Queue status, parse status, artifacts, and errors remain separate in the job
  contract; a completed transport request does not imply a successful parse.
- Authentication is optional at the FileX boundary. Use a bearer token or put
  the service behind an authenticated gateway for untrusted networks.

## Evaluation

The pinned ParseBench suite contains 2,553 cases and all five dimensions have
reached a trusted terminal state. The older 5.30% layout number is retired
because it mixed 458 legacy contract zeros with 42 current Document IR results;
it is not a valid score for the current runtime. A fixed 20-case current-layout
A/B improved from **62.31% to 76.71%** after the Document IR v3 repair (11
improved, 9 unchanged, 0 regressed). The subsequent clean 500-case layout run
scored **71.70%**.

| Dimension | Cases | FileX score | Official PaddleOCR-VL-1.6 reference |
| --- | ---: | ---: | ---: |
| Tables | 503 | 67.64% | 67.77% |
| Charts | 568 | 56.14% | 54.24% |
| Content faithfulness | 506 | 82.88% | 82.71% |
| Semantic formatting | 476 | 48.40% | 54.64% |
| Visual grounding / layout | 500 | **71.70%** | 77.80% |
| Equal-weight overall | 2,553 | **65.35%** | **67.43%** |

Nineteen formatting cases returned `not_scored` and are excluded rather than
counted as zero. The overall row is the equal-weight mean of the five dimension
means; the case-weighted mean over 2,534 numeric results is 65.44%. See
[the ParseBench evaluation report](docs/parsebench-evaluation.md) for pinned
revisions, methodology, limitations, and the optimization roadmap.

## Security and production checklist

- Keep provider keys and service tokens outside Git and container layers.
- Mount the workspace on persistent storage and apply a retention policy to job
  sources and artifacts.
- Do not expose the unauthenticated service directly to an untrusted network.
- Keep `FILEX_SERVICE_SOURCE_URL_HOSTS` narrow; prefer short-lived, read-only
  pre-signed URLs with expected size and SHA-256.
- Start with concurrency 1 per GPU and scale with multiple replicas only after
  measuring VRAM and queue latency.
- Monitor `/healthz`, queue depth, no-progress restarts, disk usage, and model
  warmup latency.

The GitHub workflow `.github/workflows/filex-image.yml` validates pull requests
and publishes the Linux AMD64 all-in-one image on eligible branches and tags.
