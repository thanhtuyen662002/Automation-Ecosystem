# Automation-Ecosystem

Core execution engine for durable, local-first automation workers.

## Runtime Model

- Python 3.11+
- Redis Streams consumer groups for delivery
- Supabase/PostgreSQL as source of truth
- At-least-once execution with idempotency support
- Synchronous long-running workers with structured JSON logs
- Process-enforced job timeouts; registered handlers must be pickleable

## Required Environment

```powershell
$env:DATABASE_URL="postgresql://..."
$env:REDIS_URL="redis://localhost:6379/0"
$env:ENGINE_WORKER_ID="worker-1"
```

Optional settings include `ENGINE_STREAM_NAME`, `ENGINE_CONSUMER_GROUP`,
`ENGINE_MAX_ATTEMPTS`, `ENGINE_DEFAULT_JOB_TIMEOUT_SECONDS`,
`ENGINE_HEARTBEAT_INTERVAL_SECONDS`, and `ENGINE_LEASE_TIMEOUT_SECONDS`.

## Basic Usage

```python
from automation_engine import EngineSettings, ExecutionEngine
from automation_engine.worker import Worker

settings = EngineSettings.from_env()
engine = ExecutionEngine(settings)

def send_email(payload: dict) -> dict:
    return {"sent": True, "recipient": payload["recipient"]}

engine.register_task("send_email", send_email)
Worker(engine).run_forever()
```

## Worker Runtime

The standalone worker runtime lives in `workers/runtime` and consumes Redis
Stream messages containing only `task_id` and `task_type`. Task data is always
loaded from PostgreSQL table `automation_tasks`.

Run the runtime:

```powershell
$env:PYTHONPATH="."
$env:DATABASE_URL="postgresql://..."
$env:REDIS_URL="redis://localhost:6379/0"
$env:WORKER_ID="worker-1"
python -m workers.runtime.worker_runtime
```

Copy `.env.example` to `.env` for local configuration. The runtime reads `.env`
automatically and supports `MAX_BROWSER_WORKERS`, `MAX_MEDIA_WORKERS`,
`MAX_AI_WORKERS`, `HEARTBEAT_INTERVAL`, `TASK_TIMEOUT`, and `MAX_RETRIES`.

Create the runtime table with `migrations/002_worker_runtime_tasks.sql`.

Redis message shape:

```text
task_id=<uuid>
task_type=<registered task type>
```

Enqueueing:

```python
settings = EngineSettings.from_env()
engine = ExecutionEngine(settings)
engine.open()
try:
    job = engine.enqueue_job(
        "send_email",
        {"recipient": "person@example.com"},
        idempotency_key="email:person@example.com:welcome",
    )
finally:
    engine.close()
```
