# Automation Ecosystem

Postgres-first workflow orchestration and worker execution runtime for local automation.

## Runtime Model

- Python 3.11+
- PostgreSQL/Supabase is the single source of truth
- `jobs`, `tasks`, `task_dependencies`, and `task_executions` hold orchestration and execution state
- `WorkflowManager` promotes dependency-satisfied work and dispatches through batch DB acquisition
- `WorkerRuntime` polls Postgres directly with bounded async concurrency
- Heartbeats and leases are stored on `task_executions`
- Retries are scheduled through task state: `RETRY` plus `next_retry_at`
- Structured JSON logs are emitted by the worker runtime

No external queue participates in orchestration or execution flow.

## Required Environment

```powershell
$env:DATABASE_URL="postgresql://..."
$env:WORKER_ID="worker-1"
```

Optional worker settings:

```powershell
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
```

## Database

Use `database/schema.sql` as the canonical schema. It separates orchestration state in `tasks` from attempt state in `task_executions`.

## Run A Worker

```powershell
$env:PYTHONPATH="."
python -m workers.worker_runtime
```

The default command registers real minimal handlers for `browser`, `media`, and `ai`. Production handlers should be registered in process with `WorkerRuntime.register_task(...)`; handlers must be idempotent, stateless, and retry-safe.

## Dashboard UI

The React dashboard uses Vite, TypeScript, TailwindCSS, shadcn-style local components, TanStack Query, and Axios.

```powershell
copy .env.frontend.example .env.local
npm install
npm run dev
```

Set `VITE_API_URL` in `.env.local` to the FastAPI backend URL. The production build is suitable for Electron or Tauri wrappers:

```powershell
npm run build
```

## Desktop App

Electron packaging is configured for Windows.

```powershell
npm install
npm run electron:dev
npm run electron:build
```

Desktop environment variables:

- `VITE_API_URL` controls frontend API calls.
- `AE_BACKEND_COMMAND` and `AE_BACKEND_ARGS` control backend auto-start.
- `AE_DISABLE_BACKEND_AUTOSTART=true` disables backend auto-start.

See [PACKAGING.md](PACKAGING.md) for the full one-click EXE build flow.
