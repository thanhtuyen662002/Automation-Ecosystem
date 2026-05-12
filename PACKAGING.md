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

Packaged builds must not include `.env.production`, `SUPABASE_SERVICE_KEY`,
`ADMIN_SECRET`, `LICENSE_SECRET`, or provider API keys.

License activation is handled by the Supabase Edge Function at
`supabase/functions/license-auth`:

- The desktop app ships only public values such as `LICENSE_AUTHORITY_URL` and
  `SUPABASE_PUBLISHABLE_KEY`.
- The Edge Function keeps service-role access in Supabase secrets.
- The user enters username + license key once.
- The backend stores the returned refresh token through Windows DPAPI, not in
  `.env`, SQLite, localStorage, or sessionStorage.
- On later launches the UI calls `/api/v1/auth/bootstrap`; the backend refreshes
  silently or allows the cached license for `LICENSE_OFFLINE_GRACE_DAYS`.

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

Apply the SQL in `supabase/migrations/202605120001_license_authority.sql`, then
deploy `supabase/functions/license-auth` with JWT verification disabled as shown
in `supabase/config.toml`.

Set these Edge Function secrets in Supabase:

```text
SUPABASE_URL
SUPABASE_SECRET_KEYS or SUPABASE_SERVICE_ROLE_KEY
LICENSE_OFFLINE_GRACE_DAYS=7
LICENSE_REFRESH_TOKEN_DAYS=365
```

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
