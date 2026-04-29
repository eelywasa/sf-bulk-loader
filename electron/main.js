'use strict'

const { app, BrowserWindow, dialog, ipcMain } = require('electron')
const { spawn, spawnSync } = require('child_process')
const fs = require('fs')
const http = require('http')
const net = require('net')
const path = require('path')

// ─── Config ──────────────────────────────────────────────────────────────────

const BACKEND_HOST = '127.0.0.1'

const resourcesPath = app.isPackaged
  ? process.resourcesPath
  : path.join(__dirname, '..')
const FRONTEND_INDEX = path.join(resourcesPath, 'frontend', 'dist', 'index.html')
const BACKEND_DIR = path.join(resourcesPath, 'backend')

// ─── Free port discovery ──────────────────────────────────────────────────────
// Port 8000 is a common development port. Starting from 47000 avoids collisions
// with well-known services and typical dev tooling.

function findFreePort(start = 47000) {
  return new Promise((resolve, reject) => {
    const tryPort = (port) => {
      const server = net.createServer()
      server.listen(port, '127.0.0.1', () => server.close(() => resolve(port)))
      server.on('error', () =>
        port < start + 100
          ? tryPort(port + 1)
          : reject(new Error('No free port found in range'))
      )
    }
    tryPort(start)
  })
}

// ─── Tool discovery ──────────────────────────────────────────────────────────

// In packaged mode the PyInstaller binary is at:
//   Contents/Resources/backend/sf_bulk_loader/sf_bulk_loader(.exe)
//
// In dev mode fall back to the backend venv uvicorn/alembic so developers
// do not need to run PyInstaller to iterate locally.

function findBackendBinary() {
  const binName = process.platform === 'win32' ? 'sf_bulk_loader.exe' : 'sf_bulk_loader'
  return path.join(BACKEND_DIR, 'sf_bulk_loader', binName)
}

function findVenvUvicorn() {
  const venvBin = process.platform === 'win32' ? 'Scripts' : 'bin'
  const ext = process.platform === 'win32' ? '.exe' : ''
  const venvUvicorn = path.join(BACKEND_DIR, '.venv', venvBin, `uvicorn${ext}`)
  if (fs.existsSync(venvUvicorn)) return venvUvicorn
  return 'uvicorn'
}

function findVenvAlembic() {
  const venvBin = process.platform === 'win32' ? 'Scripts' : 'bin'
  const ext = process.platform === 'win32' ? '.exe' : ''
  const venvAlembic = path.join(BACKEND_DIR, '.venv', venvBin, `alembic${ext}`)
  if (fs.existsSync(venvAlembic)) return venvAlembic
  return 'alembic'
}

// ─── Data directory setup ────────────────────────────────────────────────────

function ensureDataDirs(dataDir) {
  for (const subdir of ['', 'db', 'input', 'output', 'logs']) {
    fs.mkdirSync(path.join(dataDir, subdir), { recursive: true })
  }
}

// ─── PATH enrichment ─────────────────────────────────────────────────────────
// Electron does not inherit the user's shell PATH. Augment it with locations
// where Python tools are commonly installed so uvicorn/alembic can be found
// in dev mode when the backend venv is absent.

function enrichedPath() {
  const extra = [
    '/usr/local/bin',
    '/opt/homebrew/bin',
    '/opt/homebrew/sbin',
    `${process.env.HOME}/.pyenv/shims`,
    `${process.env.HOME}/.local/bin`,
  ]
  const current = process.env.PATH || ''
  const parts = current.split(':').filter(Boolean)
  for (const p of extra) {
    if (!parts.includes(p)) parts.push(p)
  }
  return parts.join(':')
}

// ─── Backend environment ─────────────────────────────────────────────────────

function buildBackendEnv(dataDir, port) {
  // Normalise path separators to forward slashes for the SQLite URL.
  // On Windows, path.join produces backslashes which SQLAlchemy does not accept
  // in a sqlite+aiosqlite:/// URL.
  const normalised = dataDir.replace(/\\/g, '/')

  return {
    ...process.env,
    PATH: enrichedPath(),
    APP_DISTRIBUTION: 'desktop',
    DATABASE_URL: `sqlite+aiosqlite:///${normalised}/db/bulk_loader.db`,
    ENCRYPTION_KEY_FILE: path.join(dataDir, 'db', 'encryption.key'),
    JWT_SECRET_KEY_FILE: path.join(dataDir, 'db', 'jwt_secret.key'),
    INPUT_DIR: path.join(dataDir, 'input'),
    OUTPUT_DIR: path.join(dataDir, 'output'),
    BACKEND_HOST,
    BACKEND_PORT: String(port),
  }
}

// ─── Database migrations ─────────────────────────────────────────────────────

function runMigrations(dataDir, port) {
  const env = buildBackendEnv(dataDir, port)
  let cmd, args

  if (app.isPackaged) {
    // Packaged: use the compiled binary with --migrate flag
    cmd = findBackendBinary()
    args = ['--migrate']
  } else {
    // Dev: use alembic from the backend venv
    cmd = findVenvAlembic()
    args = ['upgrade', 'head']
  }

  console.log('[electron] Running database migrations...')
  const result = spawnSync(cmd, args, {
    cwd: BACKEND_DIR,
    env,
    stdio: ['ignore', 'inherit', 'inherit'],
  })

  if (result.error) {
    console.error('[electron] Could not run migrations:', result.error.message)
    return // binary not found — log and continue; DB may already be initialised
  }
  if (result.status !== 0) {
    console.error('[electron] Migration failed with exit code', result.status)
    app.quit()
  }
}

// ─── Backend process ─────────────────────────────────────────────────────────

let backendProcess = null
let backendPort = null  // remembered across createWindow() calls so macOS activate reuses it

function startBackend(dataDir, port) {
  if (backendProcess) return  // already running — reuse on window re-open (macOS)
  const env = buildBackendEnv(dataDir, port)
  let cmd, args

  if (app.isPackaged) {
    // Packaged: run the PyInstaller binary directly — it starts uvicorn
    cmd = findBackendBinary()
    args = []
  } else {
    // Dev: use uvicorn from the backend venv
    cmd = findVenvUvicorn()
    args = ['app.main:app', '--host', BACKEND_HOST, '--port', String(port)]
  }

  backendProcess = spawn(cmd, args, { cwd: BACKEND_DIR, env })

  backendProcess.stdout.on('data', (data) => {
    process.stdout.write(`[backend] ${data}`)
  })
  backendProcess.stderr.on('data', (data) => {
    process.stderr.write(`[backend] ${data}`)
  })
  backendProcess.on('error', (err) => {
    console.error('[electron] Failed to start backend:', err.message)
    backendProcess = null
    const hint = app.isPackaged
      ? 'The backend binary could not be started. Try reinstalling the application.'
      : 'Make sure the backend virtual environment is set up:\n' +
        '  cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt'
    dialog.showErrorBox('Backend failed to start', `${err.message}\n\n${hint}`)
    app.quit()
  })

  backendProcess.on('exit', (code, signal) => {
    console.log(`[backend] process exited with code ${code} signal ${signal}`)
    backendProcess = null
    if (code !== 0 && code !== null) {
      dialog.showErrorBox(
        'Backend failed to start',
        `The backend process exited with code ${code}.`
      )
      app.quit()
    }
  })
}

function stopBackend() {
  if (backendProcess) {
    backendProcess.kill()
    backendProcess = null
  }
  backendPort = null
}

// ─── Backend health check ─────────────────────────────────────────────────────

function waitForBackend(port, maxAttempts = 30) {
  const backendUrl = `http://${BACKEND_HOST}:${port}`
  return new Promise((resolve, reject) => {
    let attempts = 0

    const check = () => {
      http
        .get(`${backendUrl}/api/health`, (res) => {
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

// ─── IPC handlers ─────────────────────────────────────────────────────────────

ipcMain.handle('dialog:openDirectory', async () => {
  const { canceled, filePaths } = await dialog.showOpenDialog({
    properties: ['openDirectory'],
  })
  return canceled ? null : filePaths[0]
})

// ─── Window ───────────────────────────────────────────────────────────────────

async function createWindow() {
  const dataDir = app.getPath('userData')
  ensureDataDirs(dataDir)

  // If a backend is already running (macOS activate flow), reuse its port —
  // probing a new one would make the renderer wait for a port the backend
  // never bound to. Otherwise honour BACKEND_PORT if explicitly set (used by
  // the packaged-app smoke test to pin to a known port), or probe for a free
  // one starting at 47000.
  let port
  if (backendProcess && backendPort) {
    port = backendPort
  } else if (process.env.BACKEND_PORT) {
    port = Number(process.env.BACKEND_PORT)
  } else {
    port = await findFreePort()
  }
  backendPort = port
  console.log(`[electron] Using backend port ${port}`)

  if (!backendProcess) runMigrations(dataDir, port)
  startBackend(dataDir, port)

  try {
    await waitForBackend(port)
  } catch (err) {
    console.error('[electron] Backend failed to start:', err.message)
    app.quit()
    return
  }

  const backendUrl = `http://${BACKEND_HOST}:${port}`

  const win = new BrowserWindow({
    width: 1400,
    height: 900,
    title: 'Salesforce Bulk Loader',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      additionalArguments: [`--backend-url=${backendUrl}`],
      // webSecurity is disabled so the file:// renderer can make requests to
      // http://127.0.0.1:{port} without CORS blocking. This is acceptable because:
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
  app.quit()
})

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow()
  }
})
