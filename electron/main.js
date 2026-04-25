'use strict'

const { app, BrowserWindow, dialog } = require('electron')
const { spawn, spawnSync } = require('child_process')
const fs = require('fs')
const http = require('http')
const path = require('path')

// ─── Config ──────────────────────────────────────────────────────────────────

const BACKEND_PORT = 8000
const BACKEND_HOST = '127.0.0.1'
const BACKEND_URL = `http://${BACKEND_HOST}:${BACKEND_PORT}`

const resourcesPath = app.isPackaged
  ? process.resourcesPath
  : path.join(__dirname, '..')
const FRONTEND_INDEX = path.join(resourcesPath, 'frontend', 'dist', 'index.html')
const BACKEND_DIR = path.join(resourcesPath, 'backend')

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

function buildBackendEnv(dataDir) {
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
  }
}

// ─── Database migrations ─────────────────────────────────────────────────────

function runMigrations(dataDir) {
  const env = buildBackendEnv(dataDir)
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

function startBackend(dataDir) {
  if (backendProcess) return  // already running — reuse on window re-open (macOS)
  const env = buildBackendEnv(dataDir)
  let cmd, args

  if (app.isPackaged) {
    // Packaged: run the PyInstaller binary directly — it starts uvicorn
    cmd = findBackendBinary()
    args = []
  } else {
    // Dev: use uvicorn from the backend venv
    cmd = findVenvUvicorn()
    args = ['app.main:app', '--host', BACKEND_HOST, '--port', String(BACKEND_PORT)]
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
    // If the backend exits before the window is created (e.g. port already in
    // use), quit immediately so the app doesn't silently connect to a stale
    // or unrelated process on the same port.
    if (code !== 0 && code !== null) {
      dialog.showErrorBox(
        'Backend failed to start',
        `The backend process exited with code ${code}. Another process may already be using port ${BACKEND_PORT}.`
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
  if (!backendProcess) runMigrations(dataDir)
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
  app.quit()
})

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow()
  }
})
