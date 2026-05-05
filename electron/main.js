import { app, BrowserWindow, dialog, ipcMain, shell } from "electron";
import { spawn } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import net from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const isDev = process.env.NODE_ENV === "development" || process.env.ELECTRON_RENDERER_URL;

let backendProcess = null;
let mainWindow = null;
let splashWindow = null;
let apiBaseUrl = "";
let logFile = "";

function createSplashWindow() {
  splashWindow = new BrowserWindow({
    width: 440,
    height: 320,
    resizable: false,
    frame: false,
    show: true,
    backgroundColor: "#f8fafc",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      additionalArguments: [`--api-base-url=${apiBaseUrl}`],
    },
  });
  splashWindow.loadFile(path.join(__dirname, "splash.html"));
}

function createMainWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 840,
    minWidth: 1080,
    minHeight: 720,
    show: false,
    title: "Automation Ecosystem",
    backgroundColor: "#f8fafc",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      additionalArguments: [`--api-base-url=${apiBaseUrl}`],
    },
  });

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: "deny" };
  });

  if (isDev) {
    mainWindow.loadURL(process.env.ELECTRON_RENDERER_URL || "http://127.0.0.1:5173");
  } else {
    mainWindow.loadFile(path.join(__dirname, "..", "dist", "index.html"));
  }

  mainWindow.once("ready-to-show", () => {
    splashWindow?.close();
    splashWindow = null;
    mainWindow?.show();
  });
}

async function bootstrap() {
  const userData = app.getPath("userData");
  const logsDir = path.join(userData, "logs");
  fs.mkdirSync(logsDir, { recursive: true });
  logFile = path.join(logsDir, "app.log");
  appendLog("desktop_bootstrap_started");

  const port = Number(process.env.APP_PORT || process.env.API_PORT) || (await findFreePort(8000));
  apiBaseUrl = `http://127.0.0.1:${port}`;
  createSplashWindow();
  await updateSplash("Starting backend", "Preparing local services...");

  const envFile = ensureProductionEnv(userData);
  startBackend({ port, envFile, logFile });
  await updateSplash("Connecting DB", "Waiting for backend health checks...");

  try {
    await waitForHealth(`${apiBaseUrl}/health`, 45000);
  } catch (error) {
    appendLog(`backend_start_failed ${error instanceof Error ? error.message : String(error)}`);
    await showStartupFailure();
    app.quit();
    return;
  }

  await updateSplash("Opening dashboard", "System is ready.");
  createMainWindow();
}

function startBackend({ port, envFile, logFile }) {
  if (process.env.AE_DISABLE_BACKEND_AUTOSTART === "true") {
    appendLog("backend_autostart_disabled");
    return;
  }
  const backendPath = resolveBackendExecutable();
  const command = process.env.AE_BACKEND_COMMAND || backendPath;
  const args = process.env.AE_BACKEND_COMMAND
    ? parseArgs(process.env.AE_BACKEND_ARGS || "")
    : isDev
      ? ["-3", "-m", "scripts.start_backend"]
      : [];

  appendLog(`starting_backend command=${command}`);
  backendProcess = spawn(command, args, {
    cwd: app.getAppPath(),
    env: {
      ...process.env,
      APP_PORT: String(port),
      API_PORT: String(port),
      API_HOST: "127.0.0.1",
      AE_ENV_FILE: envFile,
      AE_LOG_FILE: logFile,
    },
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: true,
  });

  backendProcess.stdout?.on("data", (chunk) => appendLog(chunk.toString().trim()));
  backendProcess.stderr?.on("data", (chunk) => appendLog(chunk.toString().trim()));
  backendProcess.on("exit", (code) => appendLog(`backend_exited code=${code}`));
}

function resolveBackendExecutable() {
  if (isDev) return "py";
  const resourcePath = process.resourcesPath;
  return path.join(resourcePath, "backend", "backend.exe");
}

function ensureProductionEnv(userData) {
  const envPath = path.join(userData, ".env.production");
  if (fs.existsSync(envPath)) return envPath;
  const bundledEnv = isDev
    ? path.join(app.getAppPath(), ".env.production")
    : path.join(process.resourcesPath, ".env.production");
  if (fs.existsSync(bundledEnv)) {
    fs.copyFileSync(bundledEnv, envPath);
  } else {
    fs.writeFileSync(
      envPath,
      [
        "DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/automation",
        "WORKER_ID=desktop-worker-1",
        "WORKER_MAX_CONCURRENCY=4",
        "WORKER_BATCH_SIZE=10",
        "SCHEDULER_INTERVAL_SECONDS=5",
      ].join("\n") + "\n",
      "utf-8",
    );
  }
  return envPath;
}

async function updateSplash(title, detail) {
  splashWindow?.webContents.send("startup-status", { title, detail });
}

async function waitForHealth(url, timeoutMs) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeoutMs) {
    if (await httpOk(url)) return;
    await sleep(750);
  }
  throw new Error(`Backend health check timed out: ${url}`);
}

function httpOk(url) {
  return new Promise((resolve) => {
    const request = http.get(url, (response) => {
      response.resume();
      resolve(response.statusCode >= 200 && response.statusCode < 300);
    });
    request.on("error", () => resolve(false));
    request.setTimeout(1500, () => {
      request.destroy();
      resolve(false);
    });
  });
}

function findFreePort(startPort) {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.listen(startPort, "127.0.0.1", () => {
      const address = server.address();
      const port = typeof address === "object" && address ? address.port : startPort;
      server.close(() => resolve(port));
    });
    server.on("error", () => resolve(findFreePort(startPort + 1)));
  });
}

async function showStartupFailure() {
  const result = await dialog.showMessageBox({
    type: "error",
    title: "System failed to start",
    message: "Automation Ecosystem could not start.",
    detail: `Open the log file for details:\n${logFile}`,
    buttons: ["Open logs", "Close"],
    defaultId: 0,
  });
  if (result.response === 0) shell.openPath(logFile);
}

function appendLog(message) {
  if (!logFile) return;
  fs.appendFileSync(logFile, `[${new Date().toISOString()}] ${message}\n`, "utf-8");
}

function parseArgs(value) {
  return value.trim() ? value.trim().split(/\s+/) : [];
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

app.whenReady().then(bootstrap);

ipcMain.handle("desktop:get-config", () => ({
  apiBaseUrl,
  logFile,
}));

app.on("before-quit", () => {
  if (backendProcess) {
    backendProcess.kill();
    backendProcess = null;
  }
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
