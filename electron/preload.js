import { contextBridge, ipcRenderer } from "electron";

const apiBaseArg = process.argv.find((arg) => arg.startsWith("--api-base-url="));
const apiBaseUrl = apiBaseArg ? apiBaseArg.slice("--api-base-url=".length) : "";

contextBridge.exposeInMainWorld("automationDesktop", {
  platform: process.platform,
  apiBaseUrl,
  getConfig: () => ipcRenderer.invoke("desktop:get-config"),
  onStartupStatus: (callback) => {
    const listener = (_event, payload) => callback(payload);
    ipcRenderer.on("startup-status", listener);
    return () => ipcRenderer.removeListener("startup-status", listener);
  },
});
