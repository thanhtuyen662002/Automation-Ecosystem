# Automation Ecosystem Windows Packaging

This package produces a one-click Windows desktop application:

- Electron owns the desktop window and backend lifecycle.
- PyInstaller bundles the Python backend into `backend.exe`.
- FastAPI, WorkerRuntime, and Scheduler run inside the backend process.
- React is built into `dist/` and loaded by Electron.
- Logs are written to the Electron user-data directory at `logs/app.log`.

## Runtime Flow

1. User opens `Automation Ecosystem.exe`.
2. Electron shows a splash screen: `Starting system...`.
3. Electron finds a free local port, or uses `APP_PORT`.
4. Electron creates `.env.production` in the app user-data directory if missing.
5. Electron starts `backend.exe`.
6. Electron waits for `GET /health`.
7. Electron loads the dashboard window.
8. On app quit, Electron stops the backend process.

## Database Modes

### Mode 1: External PostgreSQL

This is the default production mode. Set `DATABASE_URL` in `.env.production`:

```env
DATABASE_URL=postgresql://user:password@host:5432/automation
```

The app auto-creates a default `.env.production` file on first run. Non-technical installs can be shipped with a prefilled `.env.production` in the installer resources.

### Mode 2: Bundled PostgreSQL

The architecture supports this mode by extending `electron/main.js` to spawn a bundled Postgres binary before `backend.exe`, then setting `DATABASE_URL` to the local bundled instance.

Recommended layout:

```text
resources/
  postgres/
    bin/
    data/
```

This repository does not commit Postgres binaries. Add them during release assembly if you choose the fully offline mode.

## Build Commands

Install Python desktop tooling:

```powershell
python -m pip install -e ".[desktop]"
```

Install frontend and Electron dependencies:

```powershell
npm install
```

Build backend:

```powershell
npm run build:backend
```

Build frontend:

```powershell
npm run build:frontend
```

Build installer and portable EXE:

```powershell
npm run electron:build
```

Outputs:

```text
backend_dist/
  backend.exe
dist/
  index.html
  assets/
release/
  Automation-Ecosystem-Setup-0.1.0.exe
  Automation-Ecosystem-Portable-0.1.0.exe
```

## Environment Variables

- `APP_PORT`: preferred local API port. If unavailable, Electron finds a free port.
- `AE_ENV_FILE`: path to `.env.production`.
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
