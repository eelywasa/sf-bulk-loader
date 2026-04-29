'use strict'

const { contextBridge, ipcRenderer } = require('electron')

// Read the backend URL injected via additionalArguments; fall back to port 8000
// so that the dev server (where electronAPI is absent) still works.
const backendUrl =
  process.argv.find((a) => a.startsWith('--backend-url='))?.split('=')[1] ??
  'http://127.0.0.1:8000'

contextBridge.exposeInMainWorld('electronAPI', {
  backendUrl,
  selectDirectory: () => ipcRenderer.invoke('dialog:openDirectory'),
})
