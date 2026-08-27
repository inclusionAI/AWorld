# AWorld Cloud 私有云 Quickstart

AWorld Cloud 是一个无需 Kubernetes、通过 Docker Compose 运行的单机私有云。

请从仓库根目录执行本文所有命令。

## 组件

- Server 提供 Dashboard 和 Cloud API。
- Worker 从队列领取 Run，并通过本机 Docker 执行任务。
- CLI 通过 HTTP 调用 Server。
- SQLite 和本地目录保存状态与运行产物。

```text
浏览器 / CLI -> Server -> SQLite
                    |
                 Worker -> Docker
```

## 前置条件

- Linux 或 macOS。
- 可用的 Docker Engine 或 Docker Desktop。
- Docker Compose v2 插件。
- Bash 和 `curl`。
- 足够的磁盘空间和至少 4 GiB 的单任务可用内存。
- 可访问 GitHub、Harbor 和 Terminal-Bench 所需的公开镜像及数据源。

## 三步启动

第一步，确认 Docker daemon 和 Compose 可用。

```bash
docker version
docker compose version
```

第二步，生成并校验 `deploy/aworld-cloud/.env`，同时创建数据目录和探测 Docker socket。

```bash
./scripts/aworld-cloud-init.sh
```

第三步，构建并启动 Server 和 Worker。

```bash
docker compose --env-file deploy/aworld-cloud/.env -f deploy/aworld-cloud/docker-compose.yml up --build --detach --wait aworld-cloud-server aworld-cloud-worker
```

打开 [http://127.0.0.1:8000](http://127.0.0.1:8000) 查看 Dashboard。

如果修改了 `AWORLD_CLOUD_PORT`，请相应替换访问端口。

## Workspace → Batch → Run

Workspace 是持久执行边界，保存一个可写仓库和独立的 Codex 状态。

Batch 在一个 Workspace 中一次创建一组 Run。

Run 是一条 query 或 benchmark 任务，拥有独立状态、日志、结果和 trajectory。

当前 MVP 会串行执行同一 Workspace 中的 Run。

## 最短的两条 query Batch 示例

下面的示例使用 Compose 自带的 Cloud CLI，无需在宿主机安装 CLI。

先定义 Compose 命令并创建 Workspace。

```bash
compose=(docker compose --env-file deploy/aworld-cloud/.env -f deploy/aworld-cloud/docker-compose.yml)
WORKSPACE_JSON="$("${compose[@]}" run --rm -T aworld-cloud-cli cloud workspace create --name quickstart --profile terminal-bench)"
WORKSPACE_ID="$(printf '%s' "${WORKSPACE_JSON}" | "${compose[@]}" exec -T aworld-cloud-server python -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
```

在共享数据目录中写入包含两条 query 的 JSON 文件。

```bash
CLOUD_DATA_DIR="$(sed -n 's/^AWORLD_CLOUD_DATA_DIR=//p' deploy/aworld-cloud/.env | tail -n 1)"
RUNS_FILE="${CLOUD_DATA_DIR}/quickstart-runs.json"
cat > "${RUNS_FILE}" <<'JSON'
[
  {"task": "printf '%s\\n' 'first query'", "mode": "query", "model": null},
  {"task": "printf '%s\\n' 'second query'", "mode": "query", "model": null}
]
JSON
```

创建 Batch，并查看其状态和 Run 列表。

```bash
BATCH_JSON="$("${compose[@]}" run --rm -T aworld-cloud-cli cloud batch create --workspace-id "${WORKSPACE_ID}" --name two-queries --runs-file "${RUNS_FILE}")"
printf '%s\n' "${BATCH_JSON}"
"${compose[@]}" run --rm -T aworld-cloud-cli cloud run list --workspace-id "${WORKSPACE_ID}"
```

## 验证 Terminal-Bench

以下命令会运行真实的 `terminal-bench@2.0/fix-git` 和 verifier，并检查 reward 与 ATIF trajectory。

```bash
./scripts/verify-aworld-cloud-terminal-bench.sh
```

首次运行可能需要下载镜像和数据，因此耗时较长。

## 数据位置

数据根目录由 `deploy/aworld-cloud/.env` 中的 `AWORLD_CLOUD_DATA_DIR` 指定。

- `cloud.sqlite3` 保存 Workspace、Batch、Run 和文件清单等状态。
- `runs/<run-id>/stdout.log` 和 `stderr.log` 保存日志。
- `runs/<run-id>/result.json` 保存标准化结果。
- `runs/<run-id>/trajectory.atif.json` 保存成功 Run 的标准 ATIF trajectory。
- `verification/` 保存 Terminal-Bench 验证脚本下载的结果、日志和 trajectory。

## 停止

以下命令停止服务，但保留数据目录。

```bash
docker compose --env-file deploy/aworld-cloud/.env -f deploy/aworld-cloud/docker-compose.yml down
```

## 排障

1. 初始化找不到 Docker socket 时，先确认 `docker version` 能连接 daemon，再重跑初始化；必要时通过 `AWORLD_CLOUD_DOCKER_SOCKET=/var/run/docker.sock ./scripts/aworld-cloud-init.sh` 指定容器可访问的绝对路径。
2. Server 或 Worker 不健康时，运行 `"${compose[@]}" ps` 和 `"${compose[@]}" logs --tail=200 aworld-cloud-server aworld-cloud-worker` 查看状态与日志。
3. Run 长时间 queued 或失败时，先检查 Worker 日志、磁盘空间和镜像网络连接；当前单 Worker 并发为 `1`，前序任务会阻塞后续任务。

## MVP 边界

- 仅支持单机部署，不提供 Kubernetes、高可用或横向扩容。
- 使用 SQLite、本地文件系统和本机 Docker，不包含生产级数据库、对象存储或分布式队列。
- 当前没有认证、租户隔离、配额或策略控制，只应部署在可信主机和可信网络中。
- benchmark 仅允许 `terminal-bench@2.0/fix-git`，并默认使用 Harbor `oracle` agent。
- Dashboard 是基础运维视图，不是生产级管理或安全控制面。
