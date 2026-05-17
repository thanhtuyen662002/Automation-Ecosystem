# Automation Ecosystem

Local-first workflow orchestration and worker execution runtime for automation.

## Runtime Model

- Python 3.11+
- SQLite is the current local source of truth by default
- `jobs`, `tasks`, `task_dependencies`, and `task_executions` hold orchestration and execution state
- `WorkflowManager` promotes dependency-satisfied work and dispatches through batch DB acquisition
- `WorkerRuntime` polls the configured database directly with bounded async concurrency
- Heartbeats and leases are stored on `task_executions`
- Retries are scheduled through task state: `RETRY` plus `next_retry_at`
- Structured JSON logs are emitted by the worker runtime

No external queue participates in orchestration or execution flow.

## Required Environment

```powershell
$env:DATABASE_URL="sqlite+aiosqlite:///./data/app.db"
$env:WORKER_ID="worker-1"
```

Optional worker settings:

```powershell
$env:API_WORKER_ENABLED="true"
$env:WORKER_MAX_CONCURRENCY="4"
$env:WORKER_BATCH_SIZE="10"
$env:WORKER_POLL_INTERVAL_SECONDS="2"
$env:HEARTBEAT_INTERVAL="30"
$env:TASK_TIMEOUT="300"
$env:WORKER_LEASE_SECONDS="300"
$env:WORKER_RETRY_BASE_DELAY_SECONDS="5"
$env:WORKER_RETRY_MAX_DELAY_SECONDS="300"
$env:WORKER_MAX_PER_TASK_TYPE="2"
$env:WORKER_MAX_PER_ACCOUNT="1"
$env:WORKER_LOG_LEVEL="INFO"
$env:PUBLISH_WAIT_APPROVAL_MAX_RETRIES="288"
```

TikTok video downloads use `yt-dlp`. Keep the extractor and impersonation
dependency current, especially when TikTok starts returning 403 responses:

```powershell
python -m pip install -U yt-dlp curl-cffi
$env:TIKTOK_YTDLP_FORMAT="bestvideo*+bestaudio/best[ext=mp4]/best"
$env:TIKTOK_DOWNLOAD_TIMEOUT_SECONDS="90"
$env:TIKTOK_YTDLP_IMPERSONATE="chrome"
```

`TIKTOK_YTDLP_IMPERSONATE` is optional. Set it when yt-dlp logs
`no impersonate target is available` or TikTok blocks non-browser requests.

## Database

Use `database/schema.sql` as the canonical local SQLite schema. It separates orchestration state in `tasks` from attempt state in `task_executions`.

Supabase is used by the license service and optional integrations. It is not the runtime source of truth for jobs/tasks in the current local-first app.

## API And Worker

For development, `uvicorn api.main:app` starts the API, scheduler, and embedded worker by default so submitted jobs continue through the pipeline:

```powershell
$env:PYTHONPATH="."
uvicorn api.main:app --host 127.0.0.1 --port 8000
```

`API_WORKER_ENABLED=true` is the default. Set `API_WORKER_ENABLED=false` only when you are running a separate worker process.

`PUBLISH_WAIT_APPROVAL_MAX_RETRIES` controls how many times an auto-publish task waits for artifact approval before exhausting its task retry budget. The default is `288`.

## Run A Worker

```powershell
$env:PYTHONPATH="."
python -m workers.worker_runtime
```

The default command registers real minimal handlers for `browser`, `media`, and `ai`. Production handlers should be registered in process with `WorkerRuntime.register_task(...)`; handlers must be idempotent, stateless, and retry-safe.

## Dashboard UI

The React dashboard lives in `ui/` and uses Vite, TypeScript, local UI components, and TanStack Query.

```powershell
cd ui
copy .env.example .env.local
npm install
npm run dev
```

Set `VITE_API_BASE` in `ui/.env.local` to the FastAPI backend URL. `VITE_API_URL` is still supported for backward compatibility, but `VITE_API_BASE` wins when both are set.

The dashboard uses smart polling: active pipeline jobs refresh every few seconds, then slow down when the system is idle. This avoids manual reloads while keeping API load modest. WebSocket push is reserved for a later phase.

The production build is:

```powershell
npm run build
```

## Desktop Packaging

Windows packaging is documented in [PACKAGING.md](PACKAGING.md). The root `package.json` provides convenience wrappers for the UI:

```powershell
npm run dev
npm run build
```

Desktop/backend environment variables:

- `VITE_API_BASE` controls frontend API calls. `VITE_API_URL` is accepted for backward compatibility.
- `AE_BACKEND_COMMAND` and `AE_BACKEND_ARGS` control backend auto-start.
- `AE_DISABLE_BACKEND_AUTOSTART=true` disables backend auto-start.

See [PACKAGING.md](PACKAGING.md) for the full one-click EXE build flow.
