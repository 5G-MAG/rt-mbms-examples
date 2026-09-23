#!/usr/bin/env node
//
// hls-http-proxy.js -- serve an HTTPS HLS origin over plain HTTP on localhost.
//
// Why: the BM-SC pulls with libcurl + GnuTLS + nghttp2. CDNs like Fastly (which
// fronts RTVE) force TLS 1.3 + HTTP/2, and the BM-SC's GnuTLS handshake fails
// there ("received handshake message out of context"). Node/OpenSSL negotiates
// it fine, so this proxy does the HTTPS to the origin and re-serves everything
// to the BM-SC over plain HTTP -- no TLS on the BM-SC side. It rewrites the
// absolute origin URLs inside .m3u8 playlists to point back here so variant
// playlists and segments also flow through the proxy.
//
//   node hls-http-proxy.js [origin-host] [port]
//   default: rtvelivestream.rtve.es on 127.0.0.1:8888
//
// Then point the xMB template's applicationEntryPointURL at, e.g.:
//   http://127.0.0.1:8888/rtvesec/24h/24h_main_dvr.m3u8
//
'use strict';
const http = require('http');
const https = require('https');
const { URL } = require('url');

const ORIGIN = process.argv[2] || 'rtvelivestream.rtve.es';
const PORT = Number(process.argv[3]) || 8888;

function fetchUpstream(pathname, cb, depth = 0) {
  const req = https.get(
    { host: ORIGIN, path: pathname, timeout: 15000, headers: { 'User-Agent': 'hls-http-proxy', Accept: '*/*' } },
    (res) => {
      if ([301, 302, 303, 307, 308].includes(res.statusCode) && res.headers.location && depth < 4) {
        res.resume();
        const loc = res.headers.location;
        const p = loc.startsWith('http') ? new URL(loc).pathname + (new URL(loc).search || '') : loc;
        return fetchUpstream(p, cb, depth + 1);
      }
      cb(null, res);
    }
  );
  req.on('error', (e) => cb(e));
  req.on('timeout', () => req.destroy(new Error('upstream timeout')));
}

const server = http.createServer((req, res) => {
  fetchUpstream(req.url, (err, up) => {
    if (err) { res.writeHead(502); res.end('proxy error: ' + err.message); return; }
    const ct = up.headers['content-type'] || '';
    const isPlaylist = req.url.split('?')[0].endsWith('.m3u8') || ct.includes('mpegurl');
    if (isPlaylist) {
      let body = '';
      up.setEncoding('utf8');
      up.on('data', (c) => (body += c));
      up.on('end', () => {
        const hostHdr = req.headers.host || `127.0.0.1:${PORT}`;
        const rewritten = body.split(`https://${ORIGIN}`).join(`http://${hostHdr}`);
        res.writeHead(up.statusCode, { 'Content-Type': ct || 'application/vnd.apple.mpegurl', 'Cache-Control': 'no-cache' });
        res.end(rewritten);
      });
    } else {
      res.writeHead(up.statusCode, { 'Content-Type': ct || 'application/octet-stream' });
      up.pipe(res);
    }
  });
});

server.listen(PORT, '127.0.0.1', () =>
  console.log(`HLS HTTP proxy:  http://127.0.0.1:${PORT}  ->  https://${ORIGIN}   (Ctrl-C to stop)`)
);
