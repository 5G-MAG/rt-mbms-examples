#!/usr/bin/env node
//
// load-demo.js -- load an xMB demo template into the running portal.
// Creates the service, then each of its sessions, via the portal's xMB API.
// It does NOT activate anything -- open the portal's xMB tab and hit Activate
// to actually start broadcasting.
//
//   node load-demo.js [template.json]        (default: ./rtve-24h.json)
//
// Portal host/port and Basic-auth credentials are read from the portal's .env
// (default ~/rt-mbms-application-provider/.env; override with PORTAL_ENV=/path).
//
'use strict';
const fs = require('fs');
const http = require('http');
const path = require('path');

const tmplPath = process.argv[2] || path.join(__dirname, 'rtve-24h.json');
const def = JSON.parse(fs.readFileSync(tmplPath, 'utf8'));

// --- portal connection info from its .env ---
const envPath = process.env.PORTAL_ENV || path.join(process.env.HOME, 'rt-mbms-application-provider', '.env');
const env = {};
try {
  for (const line of fs.readFileSync(envPath, 'utf8').split('\n')) {
    const m = /^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$/.exec(line);
    if (m) env[m[1]] = m[2].replace(/^["']|["']$/g, '');
  }
} catch (e) {
  console.error(`Could not read portal .env at ${envPath}: ${e.message}`);
  process.exit(1);
}
const HOST = env.HOST || '127.0.0.1';
const PORT = Number(env.PORT) || 8080;
const AUTH = 'Basic ' + Buffer.from(`${env.AUTH_USER || 'admin'}:${env.AUTH_TOKEN || ''}`).toString('base64');

function req(method, p, body) {
  return new Promise((resolve, reject) => {
    const data = body ? JSON.stringify(body) : null;
    const r = http.request(
      { host: HOST, port: PORT, path: p, method, timeout: 15000,
        headers: { Authorization: AUTH, 'Content-Type': 'application/json',
          ...(data ? { 'Content-Length': Buffer.byteLength(data) } : {}) } },
      (res) => {
        let s = '';
        res.on('data', (c) => (s += c));
        res.on('end', () => {
          let j; try { j = JSON.parse(s); } catch { j = s; }
          if (res.statusCode >= 200 && res.statusCode < 300) resolve({ headers: res.headers, body: j });
          else reject(new Error(`HTTP ${res.statusCode}: ${typeof j === 'string' ? j : JSON.stringify(j)}`));
        });
      }
    );
    r.on('error', reject);
    r.on('timeout', () => { r.destroy(new Error('request timed out')); });
    if (data) r.write(data);
    r.end();
  });
}

// Pull a resource id out of a create response (body field or Location header).
function extractId(resp) {
  const b = resp.body || {};
  const fromBody = b.id || b.serviceId || b.sessionId || (b.service && b.service.id);
  if (fromBody) return fromBody;
  const loc = resp.headers && resp.headers.location;
  if (loc) return loc.split('/').filter(Boolean).pop();
  return undefined;
}

(async () => {
  console.log(`Loading "${def.description ? def.description.split('.')[0] : tmplPath}" into the portal at ${HOST}:${PORT}`);
  const svcResp = await req('POST', '/api/xmb/services', def.service);
  const serviceId = extractId(svcResp);
  if (!serviceId) {
    console.error('  Created the service but could not determine its id from the response:');
    console.error('   ', JSON.stringify(svcResp.body));
    console.error('  Find the service id in the portal xMB tab and add sessions there.');
    process.exit(1);
  }
  console.log(`  service created  id=${serviceId}`);
  for (const sess of def.sessions || []) {
    const sResp = await req('POST', `/api/xmb/services/${encodeURIComponent(serviceId)}/sessions`, sess);
    console.log(`  ${sess.sessionType} session created  id=${extractId(sResp) || '?'}` +
      (sess.application ? `  (ingest ${sess.application.ingestMode}, ${sess.application.applicationEntryPointURL})` : ''));
  }
  console.log('\nLoaded. Open the portal xMB tab, review the service/session, and hit Activate to start broadcasting.');
})().catch((e) => { console.error('FAILED:', e.message); process.exit(1); });
