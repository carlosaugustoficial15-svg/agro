const { app, BrowserWindow, dialog, ipcMain, safeStorage } = require('electron')
const { spawn } = require('child_process')
const fs = require('fs')
const path = require('path')
const http = require('http')

let service
let mainWindow
const root = path.resolve(__dirname, '..', '..')
const port = 8765
const hasLock = app.requestSingleInstanceLock()
if (!hasLock) app.quit()

function pythonPath() {
  if (process.env.PASE_PYTHON) return process.env.PASE_PYTHON
  const local = process.env.LOCALAPPDATA || ''
  const candidates = [path.join(local, 'Miniforge3', 'envs', 'pase-2026-07', 'python.exe'), 'python']
  return candidates.find(p => p === 'python' || fs.existsSync(p))
}

function startService() {
  service = spawn(pythonPath(), [path.join(__dirname, '..', 'service', 'server.py')], {
    cwd: root, windowsHide: true, env: { ...process.env, PASE_STUDIO_PORT: String(port) }
  })
  service.stderr.on('data', data => console.error(`[PASE service] ${data}`))
}

function waitForService(timeoutMs = 60000) {
  const started = Date.now()
  return new Promise((resolve, reject) => {
    const check = () => {
      const request = http.get(`http://127.0.0.1:${port}/health`, response => {
        response.resume()
        if (response.statusCode === 200) resolve()
        else setTimeout(check, 250)
      })
      request.on('error', () => Date.now() - started > timeoutMs ? reject(new Error('O serviço científico não iniciou.')) : setTimeout(check, 250))
      request.setTimeout(1000, () => request.destroy())
    }
    check()
  })
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1540, height: 940, minWidth: 900, minHeight: 620,
    backgroundColor: '#15191d', title: 'PASE Studio',
    webPreferences: { preload: path.join(__dirname, 'preload.cjs'), contextIsolation: true, nodeIntegration: false }
  })
  if (process.env.VITE_DEV_SERVER_URL) win.loadURL(process.env.VITE_DEV_SERVER_URL)
  else win.loadFile(path.join(__dirname, '..', 'dist', 'index.html'))
  mainWindow = win
}

ipcMain.handle('project:save', async (_event, project) => {
  const result = await dialog.showSaveDialog({ title: 'Salvar simulação', defaultPath: `${project.name || 'simulacao'}.pase-project`, filters: [{ name: 'Projeto PASE', extensions: ['pase-project'] }] })
  if (result.canceled || !result.filePath) return null
  fs.writeFileSync(result.filePath, JSON.stringify(project, null, 2), 'utf8')
  return result.filePath
})
ipcMain.handle('project:open', async () => {
  const result = await dialog.showOpenDialog({ title: 'Abrir simulação', properties: ['openFile'], filters: [{ name: 'Projeto PASE', extensions: ['pase-project'] }] })
  if (result.canceled) return null
  return JSON.parse(fs.readFileSync(result.filePaths[0], 'utf8'))
})
const secretFile = () => path.join(app.getPath('userData'), 'google-key.bin')
ipcMain.handle('secret:set-google', (_event, key) => {
  if (!safeStorage.isEncryptionAvailable()) throw new Error('Criptografia do Windows indisponível')
  fs.writeFileSync(secretFile(), safeStorage.encryptString(key)); return true
})
ipcMain.handle('secret:has-google', () => fs.existsSync(secretFile()))

app.on('second-instance', () => {
  if (mainWindow) { if (mainWindow.isMinimized()) mainWindow.restore(); mainWindow.show(); mainWindow.focus() }
})
app.whenReady().then(async () => {
  if (!hasLock) return
  startService()
  try { await waitForService(); createWindow() }
  catch (error) { dialog.showErrorBox('PASE Studio', error.message); app.quit() }
})
app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit() })
app.on('before-quit', () => { if (service) service.kill() })
