#!/usr/bin/env node
//
// media-server.js -- the local content origin this demo's BM-SC pulls from.
//
// rt-mbs-examples' equivalent (express-mock-media-server) also serves redirect and
// object-download fixtures for MBSTF development. Nothing on the MBMS path needs those:
// the BM-SC's Pull ingest issues plain HTTP GETs for the manifest and each segment. So
// this is a dependency-free static file server rather than a vendored express app plus
// its node_modules tree.
//
// Range requests are answered because it is useful to point a browser straight at this
// origin to compare unicast playback against the broadcast path; the BM-SC itself does
// not use them.
//
//   HOST=127.0.0.1 PORT=3005 ROOT=/path/to/docroot node media-server.js
//
'use strict';
const http = require('http');
const fs = require('fs');
const path = require('path');

const HOST = process.env.HOST || '127.0.0.1';
const PORT = Number(process.env.PORT) || 3005;
const ROOT = path.resolve(process.env.ROOT || process.cwd());

const MIME = {
  '.mpd': 'application/dash+xml',
  '.m3u8': 'application/vnd.apple.mpegurl',
  '.m4s': 'video/iso.segment',
  '.mp4': 'video/mp4',
  '.m4a': 'audio/mp4',
  '.ts': 'video/mp2t',
  '.aac': 'audio/aac',
  '.vtt': 'text/vtt',
  '.json': 'application/json',
  '.html': 'text/html; charset=utf-8',
  '.txt': 'text/plain; charset=utf-8',
};

function send(res, code, headers, body) {
  res.writeHead(code, headers);
  if (body) res.end(body); else res.end();
}

const server = http.createServer((req, res) => {
  if (req.method !== 'GET' && req.method !== 'HEAD') {
    return send(res, 405, { 'Content-Type': 'text/plain', Allow: 'GET, HEAD' }, 'Method Not Allowed\n');
  }

  const urlPath = decodeURIComponent((req.url || '/').split('?')[0]);
  // Resolve inside ROOT and reject anything that escapes it: a traversal in the request
  // path would otherwise serve any file the process can read.
  const target = path.resolve(ROOT, '.' + path.posix.normalize(urlPath));
  if (target !== ROOT && !target.startsWith(ROOT + path.sep)) {
    return send(res, 403, { 'Content-Type': 'text/plain' }, 'Forbidden\n');
  }

  fs.stat(target, (err, st) => {
    if (err || !st.isFile()) {
      return send(res, 404, { 'Content-Type': 'text/plain' }, 'Not Found\n');
    }
    const type = MIME[path.extname(target).toLowerCase()] || 'application/octet-stream';
    // A live presentation is rewritten in place every segment duration, so a cached
    // manifest is a stalled player and a stalled ingest.
    const base = {
      'Content-Type': type,
      'Cache-Control': 'no-store',
      'Access-Control-Allow-Origin': '*',
      'Accept-Ranges': 'bytes',
    };

    const range = req.headers.range;
    const m = range && /^bytes=(\d*)-(\d*)$/.exec(range.trim());
    if (m && (m[1] !== '' || m[2] !== '')) {
      let start = m[1] === '' ? st.size - Number(m[2]) : Number(m[1]);
      let end = m[1] === '' || m[2] === '' ? st.size - 1 : Number(m[2]);
      if (!Number.isFinite(start) || !Number.isFinite(end) || start < 0 || start > end || start >= st.size) {
        return send(res, 416, { ...base, 'Content-Range': `bytes */${st.size}` });
      }
      end = Math.min(end, st.size - 1);
      const headers = { ...base, 'Content-Length': end - start + 1, 'Content-Range': `bytes ${start}-${end}/${st.size}` };
      if (req.method === 'HEAD') return send(res, 206, headers);
      res.writeHead(206, headers);
      return fs.createReadStream(target, { start, end }).pipe(res);
    }

    const headers = { ...base, 'Content-Length': st.size };
    if (req.method === 'HEAD') return send(res, 200, headers);
    res.writeHead(200, headers);
    fs.createReadStream(target).pipe(res);
  });
});

server.listen(PORT, HOST, () => {
  console.log(`media-server: serving ${ROOT} on http://${HOST}:${PORT}/`);
});
