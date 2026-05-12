'use strict';
/**
 * SFBL-334 / SFBL-341 — Lambda@Edge GitHub OAuth gate for the test evidence dashboard.
 *
 * Runs on every CloudFront viewer-request. Flow:
 *   1. If the request carries a valid signed session cookie → pass through.
 *   2. If the request is for the OAuth callback path → exchange the code,
 *      check the user against the GitHub repo Collaborators list, issue a
 *      session cookie, redirect to the originally-requested path.
 *   3. Otherwise → 302 to GitHub OAuth authorize, with the original path
 *      signed into the `state` parameter so the callback can resume it.
 *
 * Authorization model (locked in SFBL-341):
 *   The user is admitted iff they are the **owner** of AUTHORIZED_REPO or
 *   an **explicit collaborator** on it (any role, including read-only).
 *   Verified by paginating `GET /user/repos?affiliation=owner,collaborator`
 *   with the user's access token and checking for the repo. This is the
 *   only API that distinguishes the owner + explicit collaborators from
 *   the public on a public repo — the obvious `permissions.pull` check
 *   would admit everyone with a GitHub account. The `owner` affiliation
 *   is needed because GitHub treats the repo owner separately from
 *   collaborators (a repo owner cannot add themselves as a collaborator).
 *
 * Constraints:
 *   - Lambda@Edge cannot read environment variables, so per-deploy config
 *     (AUTHORIZED_REPO, CALLBACK_URL, SECRET_NAME, SECRET_REGION) is
 *     baked in at CDK synth time by substituting the placeholders below.
 *   - The Secrets Manager secret carries only true secrets (clientId,
 *     clientSecret, sessionSigningKey). It's fetched once at cold start
 *     and cached in module scope for the life of the execution environment.
 *   - `state` is signed with the same HMAC key as session cookies (saves
 *     a separate state-signing secret) — this prevents OAuth CSRF.
 *   - All errors return HTML pages (not JSON) so a human in a browser
 *     gets a readable response.
 */

const https = require('https');
const crypto = require('crypto');
const { URL, URLSearchParams } = require('url');
const { SecretsManagerClient, GetSecretValueCommand } = require('@aws-sdk/client-secrets-manager');

// --- Synth-time substituted constants (do not edit; substituted by CDK) ---
const AUTHORIZED_REPO = '__AUTHORIZED_REPO__';
const CALLBACK_URL = '__CALLBACK_URL__';
const SECRET_NAME = '__SECRET_NAME__';
const SECRET_REGION = '__SECRET_REGION__';

// --- Derived constants ---
const CALLBACK_PATH = new URL(CALLBACK_URL).pathname;
const COOKIE_NAME = 'evidence_session';
const SESSION_TTL_SECONDS = 8 * 60 * 60; // 8 hours
const STATE_TTL_SECONDS = 10 * 60; // 10 minutes — OAuth flow must complete quickly
const MAX_REPO_PAGES = 5; // bound pagination — 500 repos covers a generous collaborator graph

// --- Module-scope cache ---
let _config = null;
let _secretsClient = null;

async function loadConfig() {
  if (_config) return _config;
  if (!_secretsClient) {
    _secretsClient = new SecretsManagerClient({ region: SECRET_REGION });
  }
  const result = await _secretsClient.send(new GetSecretValueCommand({ SecretId: SECRET_NAME }));
  if (!result.SecretString) {
    throw new Error('OAuth secret is empty — SFBL-350 J seeding may not have run.');
  }
  const parsed = JSON.parse(result.SecretString);
  if (!parsed.clientId || !parsed.clientSecret || !parsed.sessionSigningKey) {
    throw new Error(
      'OAuth secret missing required fields. Expected clientId, clientSecret, sessionSigningKey.',
    );
  }
  _config = parsed;
  return parsed;
}

// --- Cookie + signing helpers ---
function sign(body, signingKey) {
  return crypto.createHmac('sha256', signingKey).update(body).digest('base64url');
}

function signPayload(payload, signingKey) {
  const body = Buffer.from(JSON.stringify(payload)).toString('base64url');
  return `${body}.${sign(body, signingKey)}`;
}

function verifyPayload(value, signingKey, maxAgeSeconds) {
  if (!value || typeof value !== 'string' || !value.includes('.')) return null;
  const dot = value.lastIndexOf('.');
  const body = value.slice(0, dot);
  const sig = value.slice(dot + 1);
  const expected = sign(body, signingKey);
  // timingSafeEqual requires same-length buffers
  if (sig.length !== expected.length) return null;
  if (!crypto.timingSafeEqual(Buffer.from(sig), Buffer.from(expected))) return null;
  let parsed;
  try {
    parsed = JSON.parse(Buffer.from(body, 'base64url').toString());
  } catch {
    return null;
  }
  if (typeof parsed.exp !== 'number') return null;
  if (parsed.exp < Math.floor(Date.now() / 1000)) return null;
  if (maxAgeSeconds && parsed.iat && parsed.iat + maxAgeSeconds < Math.floor(Date.now() / 1000)) {
    return null;
  }
  return parsed;
}

function readCookie(headers, name) {
  const cookieHeaders = headers.cookie || [];
  for (const h of cookieHeaders) {
    for (const part of (h.value || '').split(';')) {
      const eq = part.indexOf('=');
      if (eq < 0) continue;
      const k = part.slice(0, eq).trim();
      const v = part.slice(eq + 1).trim();
      if (k === name) return v;
    }
  }
  return null;
}

// --- Response builders (CloudFront viewer-request response format) ---
function redirectResponse(location, setCookies) {
  const headers = {
    location: [{ key: 'Location', value: location }],
    'cache-control': [{ key: 'Cache-Control', value: 'no-store' }],
  };
  if (setCookies && setCookies.length > 0) {
    headers['set-cookie'] = setCookies.map((v) => ({ key: 'Set-Cookie', value: v }));
  }
  return { status: '302', statusDescription: 'Found', headers };
}

function htmlResponse(status, statusDescription, body) {
  return {
    status,
    statusDescription,
    headers: {
      'content-type': [{ key: 'Content-Type', value: 'text/html; charset=utf-8' }],
      'cache-control': [{ key: 'Cache-Control', value: 'no-store' }],
    },
    body,
  };
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

function forbiddenResponse(username) {
  return htmlResponse('403', 'Forbidden', `<!doctype html>
<html><head><meta charset="utf-8"><title>403 Forbidden</title><style>
body { font: 14px/1.5 -apple-system, system-ui, sans-serif; padding: 2em; max-width: 600px; margin: 4em auto; color: #1f2328; }
h1 { font-size: 20px; margin-bottom: 0.5em; }
code { background: #f6f8fa; padding: 0.1em 0.3em; border-radius: 3px; font-size: 12.5px; }
a { color: #0969da; }
</style></head><body>
<h1>Access denied</h1>
<p><code>${escapeHtml(username)}</code> is not a collaborator on <code>${escapeHtml(AUTHORIZED_REPO)}</code>.</p>
<p>Dashboard access is restricted to <a href="https://github.com/${escapeHtml(AUTHORIZED_REPO)}/settings/access">repository collaborators</a>. Contact the repo admin to be added.</p>
</body></html>`);
}

function configErrorResponse(message) {
  return htmlResponse('500', 'Internal Server Error', `<!doctype html>
<html><head><meta charset="utf-8"><title>500</title></head><body>
<h1>Configuration error</h1><p>${escapeHtml(message)}</p>
<p>Check Lambda@Edge CloudWatch logs in the nearest CloudFront edge region.</p>
</body></html>`);
}

// --- HTTPS helper (Node stdlib, no SDK dependency) ---
function httpsRequest(options, body) {
  return new Promise((resolve, reject) => {
    const req = https.request(options, (res) => {
      const chunks = [];
      res.on('data', (chunk) => chunks.push(chunk));
      res.on('end', () => resolve({
        statusCode: res.statusCode,
        headers: res.headers,
        body: Buffer.concat(chunks).toString('utf8'),
      }));
    });
    req.on('error', reject);
    if (body) req.write(body);
    req.end();
  });
}

// --- GitHub API calls ---
async function exchangeCodeForToken(code, config) {
  const body = new URLSearchParams({
    client_id: config.clientId,
    client_secret: config.clientSecret,
    code,
    redirect_uri: CALLBACK_URL,
  }).toString();
  const res = await httpsRequest(
    {
      hostname: 'github.com',
      path: '/login/oauth/access_token',
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
        Accept: 'application/json',
        'User-Agent': 'sfbl-test-evidence-lambda',
        'Content-Length': Buffer.byteLength(body),
      },
    },
    body,
  );
  if (res.statusCode !== 200) {
    throw new Error(`Token exchange returned HTTP ${res.statusCode}`);
  }
  const data = JSON.parse(res.body);
  if (data.error) {
    throw new Error(`OAuth error: ${data.error_description || data.error}`);
  }
  if (!data.access_token) {
    throw new Error('OAuth token exchange returned no access_token');
  }
  return data.access_token;
}

async function getUsername(token) {
  const res = await httpsRequest({
    hostname: 'api.github.com',
    path: '/user',
    method: 'GET',
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      'User-Agent': 'sfbl-test-evidence-lambda',
    },
  });
  if (res.statusCode !== 200) {
    throw new Error(`/user lookup returned HTTP ${res.statusCode}`);
  }
  return JSON.parse(res.body).login;
}

async function isOwnerOrCollaborator(token, repo) {
  const target = repo.toLowerCase();
  for (let page = 1; page <= MAX_REPO_PAGES; page++) {
    const res = await httpsRequest({
      hostname: 'api.github.com',
      path: `/user/repos?affiliation=owner,collaborator&per_page=100&page=${page}`,
      method: 'GET',
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': 'sfbl-test-evidence-lambda',
      },
    });
    if (res.statusCode !== 200) {
      throw new Error(`Collaborator check returned HTTP ${res.statusCode}`);
    }
    const repos = JSON.parse(res.body);
    if (!Array.isArray(repos)) {
      throw new Error('Collaborator check returned non-array');
    }
    if (repos.some((r) => r && typeof r.full_name === 'string' && r.full_name.toLowerCase() === target)) {
      return true;
    }
    if (repos.length < 100) return false;
  }
  return false;
}

// --- Main viewer-request handler ---
exports.handler = async (event) => {
  const request = event.Records[0].cf.request;
  const headers = request.headers;

  let config;
  try {
    config = await loadConfig();
  } catch (err) {
    console.error('Config load failed:', err);
    return configErrorResponse(err.message);
  }

  // OAuth callback path
  if (request.uri === CALLBACK_PATH) {
    return handleCallback(request, config);
  }

  // Existing session
  const sessionCookie = readCookie(headers, COOKIE_NAME);
  const session = verifyPayload(sessionCookie, config.sessionSigningKey, SESSION_TTL_SECONDS);
  if (session) {
    return request; // pass through to S3 origin
  }

  // No session — bounce to GitHub OAuth
  const state = signPayload(
    { returnTo: request.uri || '/', iat: Math.floor(Date.now() / 1000), exp: Math.floor(Date.now() / 1000) + STATE_TTL_SECONDS },
    config.sessionSigningKey,
  );
  const authorizeUrl = `https://github.com/login/oauth/authorize?` + new URLSearchParams({
    client_id: config.clientId,
    redirect_uri: CALLBACK_URL,
    scope: 'read:user public_repo',
    state,
    allow_signup: 'false',
  });
  return redirectResponse(authorizeUrl);
};

async function handleCallback(request, config) {
  const params = new URLSearchParams(request.querystring || '');
  const code = params.get('code');
  const state = params.get('state');

  if (!code || !state) {
    return htmlResponse('400', 'Bad Request', '<h1>Missing code or state</h1>');
  }

  const statePayload = verifyPayload(state, config.sessionSigningKey, STATE_TTL_SECONDS);
  if (!statePayload || !statePayload.returnTo) {
    return htmlResponse('400', 'Bad Request', '<h1>Invalid or expired state</h1><p>Try opening the original link again.</p>');
  }

  try {
    const token = await exchangeCodeForToken(code, config);
    const username = await getUsername(token);
    const ok = await isOwnerOrCollaborator(token, AUTHORIZED_REPO);
    if (!ok) {
      return forbiddenResponse(username);
    }

    const now = Math.floor(Date.now() / 1000);
    const cookieValue = signPayload(
      { u: username, iat: now, exp: now + SESSION_TTL_SECONDS },
      config.sessionSigningKey,
    );
    const cookieHeader =
      `${COOKIE_NAME}=${cookieValue}; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=${SESSION_TTL_SECONDS}`;

    return redirectResponse(statePayload.returnTo, [cookieHeader]);
  } catch (err) {
    console.error('OAuth callback failed:', err);
    return htmlResponse('500', 'Internal Server Error', `<h1>Authentication error</h1><p>${escapeHtml(err.message)}</p>`);
  }
}
