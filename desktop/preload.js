const { contextBridge, ipcRenderer } = require("electron");

const backendArg = process.argv.find((arg) => arg.startsWith("--backend-url="));
const backendUrl = backendArg ? backendArg.replace("--backend-url=", "") : "http://127.0.0.1:8766";
const tokenArg = process.argv.find((arg) => arg.startsWith("--runtime-token="));
const runtimeToken = tokenArg ? tokenArg.replace("--runtime-token=", "") : "";

contextBridge.exposeInMainWorld("eegDesktop", {
  backendUrl,
  runtimeToken,
  listSources: () => ipcRenderer.invoke("desktop:list-sources"),
  captureSource: (sourceId) => ipcRenderer.invoke("desktop:capture-source", sourceId),
  capturePrimaryScreen: () => ipcRenderer.invoke("desktop:capture-primary-screen"),
  readGlobalInput: () => ipcRenderer.invoke("desktop:read-global-input"),
  getActiveWindow: () => ipcRenderer.invoke("desktop:get-active-window"),
});

