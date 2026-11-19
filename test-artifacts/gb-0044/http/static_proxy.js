// 本地 E2E 用：静态托管 frontend，并把 /api 代理到 uvicorn，模拟 compose 中的 nginx。
const http = require('http');
const fs = require('fs');
const path = require('path');

const ROOT = process.env.FRONTEND_ROOT || '/workspace/frontend';
const API = process.env.API_ORIGIN || 'http://127.0.0.1:19300';
const PORT = Number(process.env.STATIC_PORT || 18300);
const TYPES = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8', '.json': 'application/json', '.svg': 'image/svg+xml',
};

http.createServer((req, res) => {
  if (req.url.startsWith('/api/')) {
    const target = API + req.url.slice(4);
    const proxy = http.request(target, { method: req.method, headers: req.headers }, pr => {
      res.writeHead(pr.statusCode, pr.headers);
      pr.pipe(res);
    });
    proxy.on('error', e => { res.writeHead(502); res.end(String(e)); });
    req.pipe(proxy);
    return;
  }
  let urlPath = req.url.split('?')[0];
  if (urlPath === '/') urlPath = '/index.html';
  const file = path.normalize(path.join(ROOT, urlPath));
  if (!file.startsWith(ROOT)) { res.writeHead(403); return res.end('forbidden'); }
  fs.readFile(file, (err, data) => {
    if (err) { res.writeHead(404); return res.end('not found'); }
    res.writeHead(200, { 'Content-Type': TYPES[path.extname(file)] || 'application/octet-stream' });
    res.end(data);
  });
}).listen(PORT, () => console.log('static+proxy on', PORT));
