'use strict'

const { spawn, spawnSync } = require('child_process')
const fs = require('fs')
const http = require('http')
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

async function main() {
  const executablePath = resolveExecutablePath()
  console.log(`[smoke] Launching ${executablePath}`)
  const launchArgs = platform === 'linux' ? ['--no-sandbox'] : []

  const child = spawn(executablePath, launchArgs, {
    cwd: rootDir,
    stdio: 'inherit',
    detached: process.platform !== 'win32',
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
  } finally {
    stopProcess(child)
  }
}

main().catch((error) => {
  console.error(`[smoke] ${error.message}`)
  process.exit(1)
})
