const { contextBridge, ipcRenderer } = require('electron')
contextBridge.exposeInMainWorld('studio', {
  saveProject: (project) => ipcRenderer.invoke('project:save', project),
  openProject: () => ipcRenderer.invoke('project:open'),
  setGoogleKey: (key) => ipcRenderer.invoke('secret:set-google', key),
  hasGoogleKey: () => ipcRenderer.invoke('secret:has-google')
})
