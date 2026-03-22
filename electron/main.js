'use strict'

const { app, BrowserWindow } = require('electron')
const { spawn } = require('child_process')
const fs = require('fs')
const http = require('http')
const path = require('path')

// ─── Config ──────────────────────────────────────────────────────────────────

const BACKEND_PORT = 8000
const BACKEND_HOST = '127.0.0.1'
const BACKEND_URL = `http://${BACKEND_HOST}:${BACKEND_PORT}`

const FRONTEND_INDEX = path.join(__dirname, '..', 'frontend', 'dist', 'index.html')
const BACKEND_DIR = path.join(__dirname, '..', 'backend')

// ─── Uvicorn discovery ───────────────────────────────────────────────────────

function findUvicorn() {
  const venvUvicorn = path.join(BACKEND_DIR, '.venv', 'bin', 'uvicorn')
  if (fs.existsSync(venvUvicorn)) {
    return venvUvicorn
  }
  return 'uvicorn'
}

// ─── Data directory setup ────────────────────────────────────────────────────

function ensureDataDirs(dataDir) {
  for (const subdir of ['', 'input', 'output']) {
    fs.mkdirSync(path.join(dataDir, subdir), { recursive: true })
  }
}

// ─── Backend process ─────────────────────────────────────────────────────────

let backendProcess = null

function startBackend(dataDir) {
  const uvicorn = findUvicorn()

  const env = {
    ...process.env,
    APP_DISTRIBUTION: 'desktop',
    DATABASE_URL: `sqlite+aiosqlite:///${dataDir}/bulk_loader.db`,
    ENCRYPTION_KEY_FILE: path.join(dataDir, 'encryption.key'),
    JWT_SECRET_KEY_FILE: path.join(dataDir, 'jwt_secret.key'),
    INPUT_DIR: path.join(dataDir, 'input'),
    OUTPUT_DIR: path.join(dataDir, 'output'),
  }

  backendProcess = spawn(
    uvicorn,
    ['app.main:app', '--host', BACKEND_HOST, '--port', String(BACKEND_PORT)],
    { cwd: BACKEND_DIR, env },
  )

  backendProcess.stdout.on('data', (data) => {
    process.stdout.write(`[backend] ${data}`)
  })
  backendProcess.stderr.on('data', (data) => {
    process.stderr.write(`[backend] ${data}`)
  })
  backendProcess.on('exit', (code) => {
    console.log(`[backend] process exited with code ${code}`)
    backendProcess = null
  })
}

function stopBackend() {
  if (backendProcess) {
    backendProcess.kill()
    backendProcess = null
  }
}

// ─── Backend health check ─────────────────────────────────────────────────────

function waitForBackend(maxAttempts = 30) {
  return new Promise((resolve, reject) => {
    let attempts = 0

    const check = () => {
      http
        .get(`${BACKEND_URL}/api/health`, (res) => {
          if (res.statusCode === 200) {
            resolve()
          } else {
            retry()
          }
          res.resume()
        })
        .on('error', retry)
    }

    const retry = () => {
      attempts++
      if (attempts >= maxAttempts) {
        reject(new Error(`Backend did not become ready after ${maxAttempts} attempts`))
      } else {
        setTimeout(check, 1000)
      }
    }

    check()
  })
}

// ─── Window ───────────────────────────────────────────────────────────────────

async function createWindow() {
  const dataDir = app.getPath('userData')
  ensureDataDirs(dataDir)
  startBackend(dataDir)

  try {
    await waitForBackend()
  } catch (err) {
    console.error('[electron] Backend failed to start:', err.message)
    app.quit()
    return
  }

  const win = new BrowserWindow({
    width: 1400,
    height: 900,
    title: 'Salesforce Bulk Loader',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      // webSecurity is disabled so the file:// renderer can make requests to
      // http://127.0.0.1:8000 without CORS blocking. This is acceptable because:
      //   - the backend binds to loopback only (127.0.0.1)
      //   - the desktop profile uses auth_mode=none (no credentials to steal)
      //   - no network-accessible API is exposed
      webSecurity: false,
    },
  })

  win.loadFile(FRONTEND_INDEX)
}

// ─── App lifecycle ────────────────────────────────────────────────────────────

app.whenReady().then(createWindow)

app.on('before-quit', stopBackend)

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit()
  }
})

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow()
  }
})
