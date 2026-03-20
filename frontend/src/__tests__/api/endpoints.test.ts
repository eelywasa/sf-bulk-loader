/**
 * Endpoint tests verify that each api function:
 *  - calls the correct HTTP method and URL
 *  - serialises request bodies as JSON
 *  - builds query strings from filter objects
 *  - returns the parsed response
 *
 * fetch is stubbed at the global level; no network calls are made.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import {
  connectionsApi,
  inputConnectionsApi,
  plansApi,
  stepsApi,
  runsApi,
  jobsApi,
  filesApi,
  healthApi,
} from '../../api/endpoints'

function mockJson(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function mockEmpty(status = 204): Response {
  return new Response(null, { status })
}

function captureLastFetch(): { url: string; init: RequestInit } {
  const [url, init] = vi.mocked(fetch).mock.lastCall as [string, RequestInit]
  return { url, init }
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn())
})

afterEach(() => {
  vi.unstubAllGlobals()
})

// ─── Connections ──────────────────────────────────────────────────────────────

describe('connectionsApi', () => {
  const conn = { id: 'c1', name: 'Prod', instance_url: 'https://org.my.salesforce.com' }

  it('list → GET /api/connections/', async () => {
    vi.mocked(fetch).mockResolvedValue(mockJson([conn]))
    const result = await connectionsApi.list()
    const { url, init } = captureLastFetch()
    expect(url).toBe('/api/connections/')
    expect(init.method).toBe('GET')
    expect(result).toEqual([conn])
  })

  it('create → POST /api/connections/ with body', async () => {
    vi.mocked(fetch).mockResolvedValue(mockJson(conn))
    const payload = { name: 'Prod', instance_url: 'https://x.salesforce.com', login_url: 'https://login.salesforce.com', client_id: 'cid', private_key: 'pk', username: 'u@e.com' }
    await connectionsApi.create(payload)
    const { url, init } = captureLastFetch()
    expect(url).toBe('/api/connections/')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body as string)).toEqual(payload)
  })

  it('update → PUT /api/connections/{id}', async () => {
    vi.mocked(fetch).mockResolvedValue(mockJson(conn))
    await connectionsApi.update('c1', { name: 'Updated' })
    const { url, init } = captureLastFetch()
    expect(url).toBe('/api/connections/c1')
    expect(init.method).toBe('PUT')
    expect(JSON.parse(init.body as string)).toEqual({ name: 'Updated' })
  })

  it('delete → DELETE /api/connections/{id}', async () => {
    vi.mocked(fetch).mockResolvedValue(mockEmpty())
    await connectionsApi.delete('c1')
    const { url, init } = captureLastFetch()
    expect(url).toBe('/api/connections/c1')
    expect(init.method).toBe('DELETE')
  })

  it('test → POST /api/connections/{id}/test', async () => {
    const testResp = { success: true, message: 'OK' }
    vi.mocked(fetch).mockResolvedValue(mockJson(testResp))
    const result = await connectionsApi.test('c1')
    const { url, init } = captureLastFetch()
    expect(url).toBe('/api/connections/c1/test')
    expect(init.method).toBe('POST')
    expect(result).toEqual(testResp)
  })
})

describe('inputConnectionsApi', () => {
  const inputConnection = { id: 'ic1', name: 'S3 Source', provider: 's3' }

  it('list → GET /api/input-connections/', async () => {
    vi.mocked(fetch).mockResolvedValue(mockJson([inputConnection]))
    const result = await inputConnectionsApi.list()
    const { url, init } = captureLastFetch()
    expect(url).toBe('/api/input-connections/')
    expect(init.method).toBe('GET')
    expect(result).toEqual([inputConnection])
  })
})

// ─── Load Plans ───────────────────────────────────────────────────────────────

describe('plansApi', () => {
  const plan = { id: 'p1', name: 'Plan A', connection_id: 'c1' }
  const run = { id: 'r1', load_plan_id: 'p1', status: 'pending' }

  it('list → GET /api/load-plans/', async () => {
    vi.mocked(fetch).mockResolvedValue(mockJson([plan]))
    await plansApi.list()
    const { url, init } = captureLastFetch()
    expect(url).toBe('/api/load-plans/')
    expect(init.method).toBe('GET')
  })

  it('create → POST /api/load-plans/', async () => {
    vi.mocked(fetch).mockResolvedValue(mockJson(plan))
    await plansApi.create({ name: 'Plan A', connection_id: 'c1' })
    const { url, init } = captureLastFetch()
    expect(url).toBe('/api/load-plans/')
    expect(init.method).toBe('POST')
  })

  it('get → GET /api/load-plans/{id}', async () => {
    vi.mocked(fetch).mockResolvedValue(mockJson({ ...plan, load_steps: [] }))
    await plansApi.get('p1')
    const { url } = captureLastFetch()
    expect(url).toBe('/api/load-plans/p1')
  })

  it('update → PUT /api/load-plans/{id}', async () => {
    vi.mocked(fetch).mockResolvedValue(mockJson(plan))
    await plansApi.update('p1', { name: 'Renamed' })
    const { url, init } = captureLastFetch()
    expect(url).toBe('/api/load-plans/p1')
    expect(init.method).toBe('PUT')
  })

  it('delete → DELETE /api/load-plans/{id}', async () => {
    vi.mocked(fetch).mockResolvedValue(mockEmpty())
    await plansApi.delete('p1')
    const { url, init } = captureLastFetch()
    expect(url).toBe('/api/load-plans/p1')
    expect(init.method).toBe('DELETE')
  })

  it('startRun → POST /api/load-plans/{id}/run', async () => {
    vi.mocked(fetch).mockResolvedValue(mockJson(run))
    const result = await plansApi.startRun('p1')
    const { url, init } = captureLastFetch()
    expect(url).toBe('/api/load-plans/p1/run')
    expect(init.method).toBe('POST')
    expect(result).toEqual(run)
  })
})

// ─── Load Steps ───────────────────────────────────────────────────────────────

describe('stepsApi', () => {
  const step = { id: 's1', load_plan_id: 'p1', sequence: 1, object_name: 'Account', operation: 'insert' }

  it('create → POST /api/load-plans/{planId}/steps', async () => {
    vi.mocked(fetch).mockResolvedValue(mockJson(step))
    await stepsApi.create('p1', { object_name: 'Account', operation: 'insert', csv_file_pattern: 'accounts_*.csv' })
    const { url, init } = captureLastFetch()
    expect(url).toBe('/api/load-plans/p1/steps')
    expect(init.method).toBe('POST')
  })

  it('update → PUT /api/load-plans/{planId}/steps/{stepId}', async () => {
    vi.mocked(fetch).mockResolvedValue(mockJson(step))
    await stepsApi.update('p1', 's1', { object_name: 'Contact' })
    const { url, init } = captureLastFetch()
    expect(url).toBe('/api/load-plans/p1/steps/s1')
    expect(init.method).toBe('PUT')
  })

  it('delete → DELETE /api/load-plans/{planId}/steps/{stepId}', async () => {
    vi.mocked(fetch).mockResolvedValue(mockEmpty())
    await stepsApi.delete('p1', 's1')
    const { url, init } = captureLastFetch()
    expect(url).toBe('/api/load-plans/p1/steps/s1')
    expect(init.method).toBe('DELETE')
  })

  it('reorder → POST /api/load-plans/{planId}/steps/reorder with ordered IDs', async () => {
    vi.mocked(fetch).mockResolvedValue(mockJson([step]))
    await stepsApi.reorder('p1', ['s2', 's1'])
    const { url, init } = captureLastFetch()
    expect(url).toBe('/api/load-plans/p1/steps/reorder')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body as string)).toEqual(['s2', 's1'])
  })

  it('preview → POST /api/load-plans/{planId}/steps/{stepId}/preview', async () => {
    const preview = { pattern: '*.csv', matched_files: [], total_rows: 0 }
    vi.mocked(fetch).mockResolvedValue(mockJson(preview))
    const result = await stepsApi.preview('p1', 's1')
    const { url, init } = captureLastFetch()
    expect(url).toBe('/api/load-plans/p1/steps/s1/preview')
    expect(init.method).toBe('POST')
    expect(result).toEqual(preview)
  })
})

// ─── Runs ─────────────────────────────────────────────────────────────────────

describe('runsApi', () => {
  const run = { id: 'r1', load_plan_id: 'p1', status: 'running' }

  it('list with no filters → GET /api/runs/', async () => {
    vi.mocked(fetch).mockResolvedValue(mockJson([run]))
    await runsApi.list()
    const { url } = captureLastFetch()
    expect(url).toBe('/api/runs/')
  })

  it('list with plan_id filter → includes query string', async () => {
    vi.mocked(fetch).mockResolvedValue(mockJson([run]))
    await runsApi.list({ plan_id: 'p1' })
    const { url } = captureLastFetch()
    expect(url).toContain('plan_id=p1')
  })

  it('list with run_status filter → includes query string', async () => {
    vi.mocked(fetch).mockResolvedValue(mockJson([run]))
    await runsApi.list({ run_status: 'running' })
    const { url } = captureLastFetch()
    expect(url).toContain('run_status=running')
  })

  it('list with multiple filters → includes all in query string', async () => {
    vi.mocked(fetch).mockResolvedValue(mockJson([]))
    await runsApi.list({ plan_id: 'p1', run_status: 'completed', started_after: '2026-01-01' })
    const { url } = captureLastFetch()
    expect(url).toContain('plan_id=p1')
    expect(url).toContain('run_status=completed')
    expect(url).toContain('started_after=2026-01-01')
  })

  it('get → GET /api/runs/{id}', async () => {
    vi.mocked(fetch).mockResolvedValue(mockJson(run))
    await runsApi.get('r1')
    const { url } = captureLastFetch()
    expect(url).toBe('/api/runs/r1')
  })

  it('jobs with no filters → GET /api/runs/{id}/jobs', async () => {
    vi.mocked(fetch).mockResolvedValue(mockJson([]))
    await runsApi.jobs('r1')
    const { url } = captureLastFetch()
    expect(url).toBe('/api/runs/r1/jobs')
  })

  it('jobs with step_id filter → includes query string', async () => {
    vi.mocked(fetch).mockResolvedValue(mockJson([]))
    await runsApi.jobs('r1', { step_id: 's1' })
    const { url } = captureLastFetch()
    expect(url).toContain('step_id=s1')
  })

  it('abort → POST /api/runs/{id}/abort', async () => {
    vi.mocked(fetch).mockResolvedValue(mockJson({ ...run, status: 'aborted' }))
    await runsApi.abort('r1')
    const { url, init } = captureLastFetch()
    expect(url).toBe('/api/runs/r1/abort')
    expect(init.method).toBe('POST')
  })
})

// ─── Jobs ─────────────────────────────────────────────────────────────────────

describe('jobsApi', () => {
  it('get → GET /api/jobs/{id}', async () => {
    const job = { id: 'j1', load_run_id: 'r1', status: 'job_complete' }
    vi.mocked(fetch).mockResolvedValue(mockJson(job))
    const result = await jobsApi.get('j1')
    const { url } = captureLastFetch()
    expect(url).toBe('/api/jobs/j1')
    expect(result).toEqual(job)
  })

  it('successCsvUrl returns correct path (no fetch)', () => {
    expect(jobsApi.successCsvUrl('j1')).toBe('/api/jobs/j1/success-csv')
  })

  it('errorCsvUrl returns correct path (no fetch)', () => {
    expect(jobsApi.errorCsvUrl('j1')).toBe('/api/jobs/j1/error-csv')
  })

  it('unprocessedCsvUrl returns correct path (no fetch)', () => {
    expect(jobsApi.unprocessedCsvUrl('j1')).toBe('/api/jobs/j1/unprocessed-csv')
  })
})

describe('filesApi', () => {
  it('listInput with no arg → GET /api/files/input', async () => {
    vi.mocked(fetch).mockResolvedValue(mockJson([]))
    await filesApi.listInput()
    const { url } = captureLastFetch()
    expect(url).toBe('/api/files/input')
  })

  it('listInput with path → GET /api/files/input?path=subdir', async () => {
    vi.mocked(fetch).mockResolvedValue(mockJson([]))
    await filesApi.listInput('subdir')
    const { url } = captureLastFetch()
    expect(url).toBe('/api/files/input?path=subdir')
  })

  it('listInput encodes path with special characters', async () => {
    vi.mocked(fetch).mockResolvedValue(mockJson([]))
    await filesApi.listInput('my folder')
    const { url } = captureLastFetch()
    expect(url).toBe('/api/files/input?path=my+folder')
  })

  it('listInput omits source for local', async () => {
    vi.mocked(fetch).mockResolvedValue(mockJson([]))
    await filesApi.listInput('nested')
    const { url, init } = captureLastFetch()
    expect(url).toBe('/api/files/input?path=nested')
    expect(init.method).toBe('GET')
  })

  it('listInput includes source for remote sources', async () => {
    vi.mocked(fetch).mockResolvedValue(mockJson([]))
    await filesApi.listInput('nested', 'ic-1')
    const { url } = captureLastFetch()
    expect(url).toBe('/api/files/input?path=nested&source=ic-1')
  })

  it('previewInput omits source for local', async () => {
    vi.mocked(fetch).mockResolvedValue(mockJson({}))
    await filesApi.previewInput('accounts.csv', 25)
    const { url } = captureLastFetch()
    expect(url).toBe('/api/files/input/accounts.csv/preview?rows=25')
  })

  it('previewInput → GET /api/files/input/{filename}/preview?rows=25', async () => {
    vi.mocked(fetch).mockResolvedValue(
      mockJson({ filename: 'accounts.csv', header: ['Name'], rows: [], row_count: 0 }),
    )
    await filesApi.previewInput('accounts.csv')
    const { url } = captureLastFetch()
    expect(url).toBe('/api/files/input/accounts.csv/preview?rows=25')
  })

  it('previewInput encodes filename with spaces', async () => {
    vi.mocked(fetch).mockResolvedValue(
      mockJson({ filename: 'my file.csv', header: [], rows: [], row_count: 0 }),
    )
    await filesApi.previewInput('my file.csv')
    const { url } = captureLastFetch()
    expect(url).toBe('/api/files/input/my%20file.csv/preview?rows=25')
  })

  it('previewInput with subdirectory path encodes each segment', async () => {
    vi.mocked(fetch).mockResolvedValue(
      mockJson({ filename: 'sub/my file.csv', header: [], rows: [], row_count: 0 }),
    )
    await filesApi.previewInput('sub/my file.csv')
    const { url } = captureLastFetch()
    expect(url).toBe('/api/files/input/sub/my%20file.csv/preview?rows=25')
  })

  it('previewInput respects custom rows param', async () => {
    vi.mocked(fetch).mockResolvedValue(
      mockJson({ filename: 'f.csv', header: [], rows: [], row_count: 0 }),
    )
    await filesApi.previewInput('f.csv', 50)
    const { url } = captureLastFetch()
    expect(url).toContain('rows=50')
  })

  it('previewInput includes source for remote sources', async () => {
    vi.mocked(fetch).mockResolvedValue(mockJson({}))
    await filesApi.previewInput('folder/accounts.csv', 10, 'ic-1')
    const { url } = captureLastFetch()
    expect(url).toBe('/api/files/input/folder/accounts.csv/preview?rows=10&source=ic-1')
  })
})

// ─── Health ───────────────────────────────────────────────────────────────────

describe('healthApi', () => {
  it('get → GET /api/health', async () => {
    vi.mocked(fetch).mockResolvedValue(mockJson({ status: 'ok' }))
    const result = await healthApi.get()
    const { url } = captureLastFetch()
    expect(url).toBe('/api/health')
    expect(result.status).toBe('ok')
  })
})
