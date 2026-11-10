/**
 * serve-site.js —— 用一个极小的静态服务把 site/ 跑起来（本地预览站点用）
 *
 *   node scripts/serve-site.js [port]
 */
import http from 'node:http';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SITE = path.resolve(__dirname, '..', 'site');
const PORT = Number(process.argv[2] || process.env.SITE_PORT || 5175);

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
};

http
  .createServer(async (request, response) => {
    const pathname = new URL(request.url, 'http://127.0.0.1').pathname;
    const relative = pathname === '/' ? 'index.html' : pathname.replace(/^\/+/, '');
    const target = path.resolve(SITE, relative);
    if (!target.startsWith(SITE)) {
      response.writeHead(403).end('forbidden');
      return;
    }
    try {
      const data = await fs.readFile(target);
      response.writeHead(200, {
        'Content-Type': MIME[path.extname(target)] || 'application/octet-stream',
        'Content-Length': data.length,
      });
      response.end(data);
    } catch {
      response.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' }).end('404');
    }
  })
  .listen(PORT, '127.0.0.1', () => {
    console.log(`[site] http://127.0.0.1:${PORT}`);
  });
