# Automation Ecosystem Windows Packaging

This package produces a one-click Windows desktop application:

- Electron owns the desktop window and backend lifecycle.
- PyInstaller bundles the Python backend into `backend.exe`.
- FastAPI, WorkerRuntime, and Scheduler run inside the backend process.
- React is built into `dist/` and loaded by Electron.
- Logs are written to the Electron user-data directory at `logs/app.log`.

## Runtime Flow

1. User opens `Automation Ecosystem.exe`.
2. Electron shows a splash screen while the backend starts.
3. Electron finds a free local port, or uses `APP_PORT`.
4. The backend creates a local `.env.production` in the user-data directory if missing.
5. The generated `.env.production` contains only non-secret local runtime values.
6. Electron starts `backend.exe`.
7. Electron waits for `GET /health`.
8. Electron loads the dashboard window.
9. On app quit, Electron stops the backend process.

## License And Secrets

Packaged builds must not include `.env.production` with secrets.
Customer/local app env chỉ được có:

```env
SUPABASE_URL=https://twkqwtpgahjusofcpivw.supabase.co
SUPABASE_ANON_KEY=<public anon key>
LICENSE_API_URL=https://twkqwtpgahjusofcpivw.supabase.co/functions/v1/license-api
LICENSE_OFFLINE_GRACE_DAYS=7
LICENSE_STATUS_CACHE_TTL_SECONDS=30
```

Không được ship:
- `SUPABASE_SERVICE_ROLE_KEY`
- `LICENSE_KEY_PEPPER`
- `MACHINE_HASH_PEPPER`
- `SUPABASE_DB_URL`
- `POSTGRES_URL`
- `ADMIN_SECRET`

License activation is handled by the trusted Supabase Edge Function at
`supabase/functions/license-api`:

- The desktop app ships only public values: `SUPABASE_URL`, `SUPABASE_ANON_KEY`,
  and `LICENSE_API_URL`.
- The Edge Function keeps service-role access and hash peppers in Supabase
  secrets.
- The user enters only a license key; no username or browser session is part of
  license validation.
- The local backend stores a non-secret activation state file with license/device
  ids and offline grace timestamps. It never stores raw license keys, Supabase
  Auth tokens, service-role keys, or peppers.
- On later launches the UI calls `/api/license/status`; the local backend asks
  the Edge Function to verify or uses the local offline grace period.

## Local Database

The packaged app defaults to local SQLite:

```env
DATABASE_URL=sqlite+aiosqlite:///{APP_DATA_DIR}/data/app.db
```

This value is non-secret and is generated under the user's AppData directory.
External PostgreSQL is not recommended for user-distributed packages unless each
customer owns the database credentials.

## Build Commands

Install Python desktop tooling:

```powershell
python -m pip install -e ".[desktop]"
```

Install frontend dependencies:

```powershell
cd ui
npm install
```

Build backend:

```powershell
pyinstaller backend.spec --distpath backend_dist
```

Build frontend:

```powershell
cd ui
npm run build
```

## Supabase Setup

Apply the existing license schema migrations, then apply
`supabase/migrations/202605140001_add_transaction_safe_license_activation_rpc.sql`
to add the transaction-safe activation RPC. Deploy
`supabase/functions/license-api` with JWT verification disabled.

Deploy steps:
1. `supabase db push` hoặc apply migration RPC.
2. `supabase functions deploy license-api --no-verify-jwt`.
3. Set Edge Function Secrets.
4. `python scripts/check_license_schema.py`.
5. `python scripts/check_license_security.py`.
6. `python scripts/create_license.py` tạo key test.
7. Test Chrome/Edge.

Set these Edge Function secrets in Supabase:

```text
SUPABASE_SERVICE_ROLE_KEY
LICENSE_KEY_PEPPER
MACHINE_HASH_PEPPER
```

Verify release artifacts do not contain strong secret names:

```powershell
python scripts/check_release_secrets.py ui/dist backend_dist release
```

The command should return no matches.

## Environment Variables

- `APP_PORT`: preferred local API port. If unavailable, Electron finds a free port.
- `AE_ENV_FILE`: path to the local non-secret runtime env file.
- `AE_LOG_FILE`: path to `logs/app.log`.
- `AE_BACKEND_COMMAND`: override backend executable.
- `AE_BACKEND_ARGS`: override backend arguments.
- `AE_DISABLE_BACKEND_AUTOSTART=true`: use an already-running backend.

## Failure UX

If backend startup fails, Electron displays:

```text
System failed to start
```

The dialog includes an option to open `logs/app.log`.
