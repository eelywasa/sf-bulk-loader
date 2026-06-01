'use strict'

/**
 * Unit tests for electron/mcpRegistration.js using Node's built-in test runner.
 * Run with:  node --test electron/mcpRegistration.test.js
 *
 * No external dependencies required.
 */

const { describe, it } = require('node:test')
const assert = require('node:assert/strict')
const { buildDiscoveryPayload, mergeMcpServerConfig } = require('./mcpRegistration.js')

// ── buildDiscoveryPayload ─────────────────────────────────────────────────────

describe('buildDiscoveryPayload', () => {
  it('returns correct shape and field values', () => {
    const result = buildDiscoveryPayload('127.0.0.1', 47123, 99999)
    assert.strictEqual(result.schema_version, 1)
    assert.strictEqual(result.base_url, 'http://127.0.0.1:47123')
    assert.strictEqual(result.port, 47123)
    assert.strictEqual(result.pid, 99999)
  })

  it('constructs base_url from host and port', () => {
    const result = buildDiscoveryPayload('0.0.0.0', 8000, 1)
    assert.strictEqual(result.base_url, 'http://0.0.0.0:8000')
  })

  it('preserves port as a number', () => {
    const result = buildDiscoveryPayload('127.0.0.1', 47000, 42)
    assert.strictEqual(typeof result.port, 'number')
  })

  it('preserves pid as provided', () => {
    const result = buildDiscoveryPayload('127.0.0.1', 47000, 12345)
    assert.strictEqual(result.pid, 12345)
  })
})

// ── mergeMcpServerConfig ──────────────────────────────────────────────────────

const SAMPLE_ENTRY = { command: '/path/to/sf_bulk_loader', args: ['mcp'] }

describe('mergeMcpServerConfig', () => {
  it('creates mcpServers when config is empty object', () => {
    const result = mergeMcpServerConfig({}, SAMPLE_ENTRY)
    assert.ok(result.mcpServers, 'mcpServers should exist')
    assert.deepStrictEqual(result.mcpServers['sf-bulk-loader'], SAMPLE_ENTRY)
  })

  it('creates mcpServers when config has no mcpServers key', () => {
    const existing = { globalShortcut: 'Cmd+Shift+.' }
    const result = mergeMcpServerConfig(existing, SAMPLE_ENTRY)
    assert.ok(result.mcpServers)
    assert.deepStrictEqual(result.mcpServers['sf-bulk-loader'], SAMPLE_ENTRY)
    // preserves other top-level keys
    assert.strictEqual(result.globalShortcut, 'Cmd+Shift+.')
  })

  it('preserves sibling mcpServers entries', () => {
    const existing = {
      mcpServers: {
        'some-other-tool': { command: '/usr/bin/other', args: [] },
      },
    }
    const result = mergeMcpServerConfig(existing, SAMPLE_ENTRY)
    // sibling is preserved
    assert.deepStrictEqual(result.mcpServers['some-other-tool'], { command: '/usr/bin/other', args: [] })
    // our entry is written
    assert.deepStrictEqual(result.mcpServers['sf-bulk-loader'], SAMPLE_ENTRY)
  })

  it('overwrites only the sf-bulk-loader key on repeat calls (idempotent)', () => {
    const firstEntry = { command: '/old/path/sf_bulk_loader', args: ['mcp'] }
    const afterFirst = mergeMcpServerConfig({}, firstEntry)

    const secondEntry = { command: '/new/path/sf_bulk_loader', args: ['mcp'] }
    const afterSecond = mergeMcpServerConfig(afterFirst, secondEntry)

    assert.deepStrictEqual(afterSecond.mcpServers['sf-bulk-loader'], secondEntry)
    // two calls → same number of keys (not duplicated)
    assert.strictEqual(Object.keys(afterSecond.mcpServers).length, 1)
  })

  it('does not mutate the input object', () => {
    const existing = { mcpServers: { 'other': { command: '/other', args: [] } } }
    const original = JSON.parse(JSON.stringify(existing))
    mergeMcpServerConfig(existing, SAMPLE_ENTRY)
    assert.deepStrictEqual(existing, original)
  })

  it('handles mcpServers: null gracefully (treats as absent)', () => {
    const existing = { mcpServers: null }
    const result = mergeMcpServerConfig(existing, SAMPLE_ENTRY)
    assert.ok(result.mcpServers)
    assert.deepStrictEqual(result.mcpServers['sf-bulk-loader'], SAMPLE_ENTRY)
  })
})
