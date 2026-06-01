'use strict'

/**
 * Pure, unit-testable helpers for SFBL-364:
 *   - buildDiscoveryPayload  — builds the mcp-discovery.json object
 *   - mergeMcpServerConfig   — idempotently merges our entry into a
 *                              claude_desktop_config.json object
 *
 * Neither function touches the filesystem, spawns processes, or reads env
 * vars.  All side-effecting code (fs reads/writes, path construction) lives
 * in main.js so these helpers can be exercised with Node's built-in test
 * runner without a real Claude install or a packaged build.
 */

/**
 * Build the discovery-file payload that matches the schema expected by
 * mcp-server/src/sf_bulk_loader_mcp/discovery.py DiscoveryFile:
 *   { schema_version: 1, base_url: string, port: number, pid: number }
 *
 * @param {string}  host         - Backend host (e.g. "127.0.0.1")
 * @param {number}  port         - Bound backend port
 * @param {number}  backendPid   - PID of the backend process (or process.pid
 *                                 as a fallback when the backend PID is
 *                                 unavailable)
 * @returns {{ schema_version: number, base_url: string, port: number, pid: number }}
 */
function buildDiscoveryPayload(host, port, backendPid) {
  return {
    schema_version: 1,
    base_url: `http://${host}:${port}`,
    port,
    pid: backendPid,
  }
}

/**
 * Idempotently merge the sf-bulk-loader MCP server entry into a parsed
 * claude_desktop_config.json object.
 *
 * Rules:
 *   - If the existing config has no `mcpServers` key, create it.
 *   - All sibling MCP server entries are preserved unchanged.
 *   - The `sf-bulk-loader` key is created or overwritten with the new entry.
 *   - The function is pure: it does not mutate the input, but returns a new
 *     top-level object (shallow-merged; mcpServers is a new object).
 *
 * @param {object} existingConfig  - Parsed JSON object from claude_desktop_config.json
 *                                   (may be {} if the file was absent).
 * @param {{ command: string, args: string[] }} entry - The MCP server entry to write.
 * @returns {object} New config object with the entry merged in.
 */
function mergeMcpServerConfig(existingConfig, entry) {
  const existingServers =
    existingConfig && typeof existingConfig.mcpServers === 'object' && existingConfig.mcpServers !== null
      ? existingConfig.mcpServers
      : {}

  return {
    ...existingConfig,
    mcpServers: {
      ...existingServers,
      'sf-bulk-loader': entry,
    },
  }
}

module.exports = { buildDiscoveryPayload, mergeMcpServerConfig }
