'use strict'

const { spawn, spawnSync } = require('child_process')
const fs = require('fs')
const http = require('http')
const os = require('os')
const path = require('path')

const rootDir = path.resolve(__dirname, '..')
const distDir = path.join(rootDir, 'dist')
const platform = process.argv[2]
const packageJson = require(path.join(rootDir, 'package.json'))
const productName = packageJson.build?.productName || 'Salesforce Bulk Loader'
const linuxExecutableName = packageJson.build?.linux?.executableName
const packageName = packageJson.name

function fail(message) {
  throw new Error(message)
}

function listFiles(dir) {
  return fs.readdirSync(dir, { withFileTypes: true })
}

function findTopLevelExecutable(dir, predicate = () => true) {
  const entry = listFiles(dir).find((item) => item.isFile() && predicate(item.name))
  if (!entry) {
    fail(`No executable candidate found in ${dir}`)
  }
  return path.join(dir, entry.name)
}

function resolveExecutablePath() {
  if (platform === 'mac') {
    const macDir = path.join(distDir, 'mac-arm64')
    const appBundle = listFiles(macDir).find((item) => item.isDirectory() && item.name.endsWith('.app'))
    if (!appBundle) {
      fail(`No .app bundle found in ${macDir}`)
    }
    const macOsDir = path.join(macDir, appBundle.name, 'Contents', 'MacOS')
    return findTopLevelExecutable(macOsDir)
  }

  if (platform === 'win') {
    const winDir = path.join(distDir, 'win-unpacked')
    return findTopLevelExecutable(winDir, (name) => name.endsWith('.exe'))
  }

  if (platform === 'linux') {
    const linuxDir = path.join(distDir, 'linux-unpacked')

    const candidateNames = [
      linuxExecutableName,
      productName,
      packageName,
      productName.toLowerCase().replace(/\s+/g, '-'),
      packageName.replace(/-desktop$/, ''),
    ].filter(Boolean)

    for (const candidateName of candidateNames) {
      const candidatePath = path.join(linuxDir, candidateName)
      if (fs.existsSync(candidatePath)) {
        return candidatePath
      }
    }

    return findTopLevelExecutable(
      linuxDir,
      (name) =>
        !name.endsWith('.so') &&
        !name.includes('.') &&
        !name.startsWith('chrome_') &&
        name !== 'chrome-sandbox',
    )
  }

  fail(`Unsupported platform '${platform}'`)
}

function httpGet(pathname) {
  return new Promise((resolve, reject) => {
    const req = http.get(
      {
        host: '127.0.0.1',
        port: 8000,
        path: pathname,
        timeout: 2000,
      },
      (res) => {
        const chunks = []
        res.on('data', (chunk) => chunks.push(chunk))
        res.on('end', () => {
          resolve({
            statusCode: res.statusCode || 0,
            body: Buffer.concat(chunks).toString('utf8'),
          })
        })
      },
    )
    req.on('error', reject)
    req.on('timeout', () => req.destroy(new Error(`Timed out fetching ${pathname}`)))
  })
}

async function waitForBackend(maxAttempts = 60) {
  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    try {
      const response = await httpGet('/api/health')
      if (response.statusCode === 200) {
        return response.body
      }
    } catch {
      // Backend is not ready yet.
    }
    await new Promise((resolve) => setTimeout(resolve, 1000))
  }
  fail(`Backend did not become ready after ${maxAttempts} attempts`)
}

function stopProcess(child) {
  if (!child || child.killed) {
    return
  }

  if (process.platform === 'win32') {
    spawnSync('taskkill', ['/pid', String(child.pid), '/t', '/f'], { stdio: 'inherit' })
    return
  }

  try {
    process.kill(-child.pid, 'SIGTERM')
  } catch {
    child.kill('SIGTERM')
  }
}

// ── MCP discovery file helpers ────────────────────────────────────────────────

/**
 * Return the OS-convention path for the Electron userData directory for the
 * given app name.  Must mirror mcp-server/src/sf_bulk_loader_mcp/discovery.py
 * and electron/main.js so the smoke can verify the same file the app writes.
 *
 * @param {string} appName - Electron productName (e.g. "Salesforce Bulk Loader")
 * @returns {string} Absolute path to the userData directory
 */
function resolveUserDataDir(appName) {
  const plat = process.platform
  if (plat === 'darwin') {
    return path.join(os.homedir(), 'Library', 'Application Support', appName)
  }
  if (plat === 'win32') {
    const appdata = process.env.APPDATA
    return appdata
      ? path.join(appdata, appName)
      : path.join(os.homedir(), 'AppData', 'Roaming', appName)
  }
  // Linux / other POSIX
  const xdg = process.env.XDG_CONFIG_HOME
  return xdg ? path.join(xdg, appName) : path.join(os.homedir(), '.config', appName)
}

/**
 * Assert that the mcp-discovery.json written by the Electron app has the
 * expected shape and is consistent with the pinned BACKEND_PORT (8000).
 *
 * Guards:
 *  - If the file does not exist, this is a hard failure (the app must write
 *    it before this function is called, i.e. after waitForBackend()).
 *  - Schema fields are validated exactly against the contract in discovery.py.
 *
 * @param {number} expectedPort - Port the app was launched with (BACKEND_PORT)
 */
function assertDiscoveryFile(expectedPort) {
  const userDataDir = resolveUserDataDir(productName)
  const discoveryPath = path.join(userDataDir, 'mcp-discovery.json')

  if (!fs.existsSync(discoveryPath)) {
    fail(`mcp-discovery.json not found at ${discoveryPath} — app must write it after backend starts`)
  }

  let discovery
  try {
    discovery = JSON.parse(fs.readFileSync(discoveryPath, 'utf8'))
  } catch (err) {
    fail(`mcp-discovery.json at ${discoveryPath} could not be parsed: ${err.message}`)
  }

  // schema_version must be exactly 1 (DiscoveryFile contract)
  if (discovery.schema_version !== 1) {
    fail(`mcp-discovery.json: expected schema_version=1, got ${discovery.schema_version}`)
  }

  // base_url must match the pinned port
  const expectedBaseUrl = `http://127.0.0.1:${expectedPort}`
  if (discovery.base_url !== expectedBaseUrl) {
    fail(`mcp-discovery.json: expected base_url="${expectedBaseUrl}", got "${discovery.base_url}"`)
  }

  // port must be a number equal to BACKEND_PORT
  if (typeof discovery.port !== 'number' || discovery.port !== expectedPort) {
    fail(`mcp-discovery.json: expected port=${expectedPort} (number), got ${JSON.stringify(discovery.port)}`)
  }

  // pid must be a positive integer
  if (typeof discovery.pid !== 'number' || !Number.isInteger(discovery.pid) || discovery.pid <= 0) {
    fail(`mcp-discovery.json: expected pid to be a positive integer, got ${JSON.stringify(discovery.pid)}`)
  }

  console.log(`[smoke] mcp-discovery.json OK — base_url=${discovery.base_url}, pid=${discovery.pid}`)
  return discovery
}

/**
 * Probe the bundled MCP binary (if present) by calling it with the `mcp`
 * subcommand and sending a single MCP initialize request over stdin, then
 * confirming the binary reads the discovery file and responds with a valid
 * initialize result.
 *
 * This is best-effort:
 *  - If the binary does not exist (e.g. a non-packaged or non-macOS build),
 *    we log a warning and skip — the binary requires a full packaged build.
 *  - A 5-second timeout prevents the smoke from hanging.
 *  - A non-zero exit code or missing `result` in the response is a hard failure.
 *
 * @param {string} appBundleDir - Root directory of the unpacked/app bundle
 *                                (used to locate the sf_bulk_loader binary)
 */
async function assertMcpBinarySmoke(appBundleDir) {
  // Locate the backend binary inside the app bundle.  In the macOS .app the
  // binary sits at Contents/Resources/backend/sf_bulk_loader/sf_bulk_loader.
  const binName = process.platform === 'win32' ? 'sf_bulk_loader.exe' : 'sf_bulk_loader'
  const mcpBinaryPath = path.join(appBundleDir, 'sf_bulk_loader', binName)

  if (!fs.existsSync(mcpBinaryPath)) {
    console.warn(`[smoke] MCP binary not found at ${mcpBinaryPath} — skipping MCP binary smoke (requires packaged build)`)
    return
  }

  console.log(`[smoke] Probing MCP binary at ${mcpBinaryPath} with 'mcp' subcommand...`)

  // Minimal MCP initialize request (JSON-RPC 2.0 over stdio)
  const initRequest = JSON.stringify({
    jsonrpc: '2.0',
    id: 1,
    method: 'initialize',
    params: {
      protocolVersion: '2024-11-05',
      capabilities: {},
      clientInfo: { name: 'smoke-test', version: '0' },
    },
  })

  await new Promise((resolve, reject) => {
    const mcpProc = spawn(mcpBinaryPath, ['mcp'], {
      stdio: ['pipe', 'pipe', 'pipe'],
      timeout: 5000,
    })

    let stdout = ''
    let stderr = ''
    let settled = false

    const done = (err) => {
      if (settled) return
      settled = true
      mcpProc.kill()
      if (err) reject(err)
      else resolve()
    }

    const timer = setTimeout(() => done(new Error('MCP binary timed out after 5 s')), 5000)

    mcpProc.stdout.on('data', (chunk) => {
      stdout += chunk.toString('utf8')
      // The MCP server sends newline-delimited JSON.  Look for the first line
      // that is a valid initialize response.
      const lines = stdout.split('\n').filter(Boolean)
      for (const line of lines) {
        try {
          const msg = JSON.parse(line)
          if (msg.id === 1 && msg.result !== undefined) {
            clearTimeout(timer)
            console.log('[smoke] MCP binary responded to initialize — discovery file read OK')
            done(null)
            return
          }
          if (msg.id === 1 && msg.error !== undefined) {
            clearTimeout(timer)
            done(new Error(`MCP binary returned error: ${JSON.stringify(msg.error)}`))
            return
          }
        } catch {
          // Not valid JSON yet — accumulate more output
        }
      }
    })

    mcpProc.stderr.on('data', (chunk) => {
      stderr += chunk.toString('utf8')
    })

    mcpProc.on('error', (err) => {
      clearTimeout(timer)
      done(new Error(`MCP binary spawn error: ${err.message}`))
    })

    mcpProc.on('exit', (code) => {
      clearTimeout(timer)
      if (!settled) {
        const detail = stderr ? ` stderr: ${stderr.slice(0, 200)}` : ''
        done(new Error(`MCP binary exited with code ${code} before responding.${detail}`))
      }
    })

    // Send the initialize request and signal end-of-stdin so the process does
    // not block waiting for more input.
    mcpProc.stdin.write(initRequest + '\n')
    mcpProc.stdin.end()
  })
}

async function main() {
  const executablePath = resolveExecutablePath()
  console.log(`[smoke] Launching ${executablePath}`)
  const launchArgs = platform === 'linux' ? ['--no-sandbox'] : []

  // Pin the backend port so the smoke test knows where to poll. main.js reads
  // BACKEND_PORT at startup and skips dynamic-port discovery when it is set.
  const SMOKE_BACKEND_PORT = 8000
  const child = spawn(executablePath, launchArgs, {
    cwd: rootDir,
    stdio: 'inherit',
    detached: process.platform !== 'win32',
    env: { ...process.env, BACKEND_PORT: String(SMOKE_BACKEND_PORT) },
  })

  process.on('exit', () => stopProcess(child))
  process.on('SIGINT', () => {
    stopProcess(child)
    process.exit(130)
  })
  process.on('SIGTERM', () => {
    stopProcess(child)
    process.exit(143)
  })

  try {
    const rawHealth = await waitForBackend()
    const health = JSON.parse(rawHealth)
    if (health.status !== 'ok') {
      fail(`/api/health returned status=${health.status}`)
    }
    console.log('[smoke] /api/health returned ok')

    const connections = await httpGet('/api/connections/')
    if (connections.statusCode !== 200) {
      fail(`/api/connections/ returned HTTP ${connections.statusCode}, expected 200`)
    }
    console.log('[smoke] /api/connections/ returned 200')

    // ── MCP discovery-file assertions (SFBL-365) ──────────────────────────────
    // The app writes mcp-discovery.json to <userData>/mcp-discovery.json after
    // the backend is healthy.  Assert the file exists with the correct shape.
    assertDiscoveryFile(SMOKE_BACKEND_PORT)

    // ── MCP binary probe (SFBL-365) ───────────────────────────────────────────
    // Locate the Resources/backend directory inside the unpacked/app bundle and
    // attempt to invoke the bundled binary with the 'mcp' subcommand.  Skipped
    // gracefully when the binary is absent (non-packaged or non-macOS build).
    let resourcesDir
    if (platform === 'mac') {
      const macDir = path.join(distDir, 'mac-arm64')
      const appBundle = fs.readdirSync(macDir, { withFileTypes: true })
        .find((item) => item.isDirectory() && item.name.endsWith('.app'))
      if (appBundle) {
        resourcesDir = path.join(macDir, appBundle.name, 'Contents', 'Resources', 'backend')
      }
    } else if (platform === 'win') {
      resourcesDir = path.join(distDir, 'win-unpacked', 'resources', 'backend')
    } else if (platform === 'linux') {
      resourcesDir = path.join(distDir, 'linux-unpacked', 'resources', 'backend')
    }

    if (resourcesDir) {
      await assertMcpBinarySmoke(resourcesDir)
    } else {
      console.warn('[smoke] Could not resolve resources dir — skipping MCP binary probe')
    }
  } finally {
    stopProcess(child)
  }
}

main().catch((error) => {
  console.error(`[smoke] ${error.message}`)
  process.exit(1)
})
