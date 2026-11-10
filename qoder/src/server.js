/**
 * server.js —— 文献罗盘 HTTP 服务
 *
 * 只用 Node 内置 http 模块 + mysql2，不引入框架，评审环境 `npm install` 后即可启动。
 * 静态前端放在 public/，同一端口提供页面与 API。
 */
import http from 'node:http';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { config } from './config.js';
import { chunkId, chunkText } from './chunk.js';
import {
  ensureSchema,
  gradeReview,
  ingest,
  listDocs,
  listDueReviews,
  scheduleReview,
  search,
  stats,
} from './db.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PUBLIC_DIR = path.resolve(__dirname, '..', 'public');

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.md': 'text/markdown; charset=utf-8',
};

function sendJson(response, status, payload) {
  const body = JSON.stringify(payload);
  response.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': Buffer.byteLength(body),
    'Access-Control-Allow-Origin': '*',
  });
  response.end(body);
}

async function readBody(request) {
  const chunks = [];
  for await (const chunk of request) chunks.push(chunk);
  if (!chunks.length) return {};
  const raw = Buffer.concat(chunks).toString('utf8');
  try {
    return JSON.parse(raw);
  } catch {
    throw new Error('请求体不是合法 JSON');
  }
}

async function serveStatic(request, response, pathname) {
  const relative = pathname === '/' ? 'index.html' : pathname.replace(/^\/+/, '');
  const target = path.resolve(PUBLIC_DIR, relative);
  if (!target.startsWith(PUBLIC_DIR)) {
    sendJson(response, 403, { ok: false, error: 'forbidden' });
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
    response.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
    response.end('404 Not Found');
  }
}

/** 把一份资料切成块并入库 */
async function ingestText({ title, source, text, userId }) {
  const chunks = chunkText(text, { chunkSize: config.chunkSize, chunkOverlap: config.chunkOverlap });
  if (!chunks.length) throw new Error('资料内容为空');
  const safeSource = source || title || 'untitled';
  const docs = chunks.map((body, index) => ({
    id: chunkId(safeSource, index),
    title: title || safeSource,
    source: safeSource,
    chunkIndex: index,
    body,
  }));
  const result = await ingest(docs);
  if (userId) await scheduleReview(userId, docs[0].id, title || safeSource);
  return { chunks: docs.length, ...result };
}

async function handleApi(request, response, url) {
  const { pathname } = url;

  if (pathname === '/api/health' && request.method === 'GET') {
    sendJson(response, 200, {
      ok: true,
      service: 'papercompass',
      seekdb: `${config.db.host}:${config.db.port}`,
      database: config.db.database,
      dim: config.dim,
      searchMode: config.searchMode,
    });
    return;
  }

  if (pathname === '/api/stats' && request.method === 'GET') {
    sendJson(response, 200, { ok: true, stats: await stats() });
    return;
  }

  if (pathname === '/api/docs' && request.method === 'GET') {
    sendJson(response, 200, { ok: true, docs: await listDocs() });
    return;
  }

  if (pathname === '/api/ingest' && request.method === 'POST') {
    const payload = await readBody(request);
    const result = await ingestText({
      title: payload.title,
      source: payload.source,
      text: payload.text,
      userId: payload.userId || null,
    });
    sendJson(response, 200, { ok: true, ...result });
    return;
  }

  if (pathname === '/api/search' && request.method === 'POST') {
    const payload = await readBody(request);
    const started = Date.now();
    const result = await search({
      query: payload.query,
      topK: payload.topK || config.topK,
      mode: payload.mode || config.searchMode,
      userId: payload.userId || null,
    });
    sendJson(response, 200, {
      ok: true,
      mode: result.mode,
      sql: result.sql,
      elapsedMs: Date.now() - started,
      hits: result.hits,
    });
    return;
  }

  if (pathname === '/api/review/due' && request.method === 'GET') {
    const userId = url.searchParams.get('user') || 'demo';
    sendJson(response, 200, { ok: true, due: await listDueReviews(userId) });
    return;
  }

  if (pathname === '/api/review/schedule' && request.method === 'POST') {
    const payload = await readBody(request);
    const result = await scheduleReview(
      payload.userId || 'demo',
      payload.docId,
      payload.title || '',
    );
    sendJson(response, 200, { ok: true, ...result });
    return;
  }

  if (pathname === '/api/review/grade' && request.method === 'POST') {
    const payload = await readBody(request);
    const result = await gradeReview(
      payload.userId || 'demo',
      payload.docId,
      payload.quality ?? 3,
      payload.title || '',
    );
    sendJson(response, 200, { ok: true, ...result });
    return;
  }

  sendJson(response, 404, { ok: false, error: `unknown endpoint ${pathname}` });
}

export function createServer() {
  return http.createServer(async (request, response) => {
    const url = new URL(request.url, `http://${request.headers.host || '127.0.0.1'}`);
    try {
      if (url.pathname.startsWith('/api/')) {
        await handleApi(request, response, url);
        return;
      }
      await serveStatic(request, response, url.pathname);
    } catch (error) {
      sendJson(response, 500, { ok: false, error: String(error.message || error) });
    }
  });
}

async function main() {
  await ensureSchema();
  const server = createServer();
  server.listen(config.httpPort, config.httpHost, () => {
    console.log(`[papercompass] http://${config.httpHost}:${config.httpPort}`);
    console.log(`[papercompass] seekdb ${config.db.host}:${config.db.port}/${config.db.database}`);
    console.log(`[papercompass] search mode = ${config.searchMode}, dim = ${config.dim}`);
  });
}

if (process.argv[1] && process.argv[1].endsWith('server.js')) {
  main().catch((error) => {
    console.error('[papercompass] 启动失败:', error);
    process.exit(1);
  });
}
