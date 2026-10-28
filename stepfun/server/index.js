/**
 * server/index.js — 极薄 Node HTTP 后端
 *
 * 职责：
 *   1. 托管静态前端（public/）、本地模型（models/）、离线 JS 运行时（/vendor/）
 *   2. 代理到 seekdb：写入文档、执行混合检索 SQL、刷新索引
 *
 * 重要：后端 **不计算任何 embedding**。
 *   文档向量与查询向量全部由浏览器内 Transformers.js 计算后随请求带上，
 *   后端只负责把这些向量与原文、元数据写入 seekdb，并把混合检索 SQL 交给 seekdb 执行。
 */
import http from 'node:http';
import fs from 'node:fs';
import fsp from 'node:fs/promises';
import path from 'node:path';
import { config, ROOT, listLocalModels } from './config.js';
import * as db from './db.js';

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.wasm': 'application/wasm',
  '.map': 'application/json; charset=utf-8',
  '.onnx': 'application/octet-stream',
  '.bin': 'application/octet-stream',
  '.txt': 'text/plain; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon',
  '.png': 'image/png',
};

/** 会话状态（集合与会话） */
const session = {
  id: `sess_${Date.now().toString(36)}`,
  startedAt: new Date().toISOString(),
  lastImportAt: null,
  lastQueryAt: null,
  lastDevice: null,
  lastDtype: null,
  importedDocs: 0,
  queryCount: 0,
};

function send(res, code, body, headers = {}) {
  const payload = typeof body === 'string' || Buffer.isBuffer(body) ? body : JSON.stringify(body);
  res.writeHead(code, {
    'Content-Type': 'application/json; charset=utf-8',
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
    'Cache-Control': 'no-store',
    ...headers,
  });
  res.end(payload);
}

function readBody(req, limitBytes = 64 * 1024 * 1024) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    req.on('data', (c) => {
      size += c.length;
      if (size > limitBytes) {
        reject(new Error('请求体过大'));
        req.destroy();
        return;
      }
      chunks.push(c);
    });
    req.on('end', () => {
      const buf = Buffer.concat(chunks);
      if (!buf.length) return resolve({});
      try {
        resolve(JSON.parse(buf.toString('utf8')));
      } catch (e) {
        reject(new Error('请求体不是合法 JSON: ' + e.message));
      }
    });
    req.on('error', reject);
  });
}

/** 静态文件服务（带路径穿越防护） */
async function serveStatic(res, root, relPath, { head = false } = {}) {
  const safe = path
    .normalize(relPath)
    .replace(/^(\.\.[\\/])+/, '')
    .replace(/^[\\/]+/, '');
  const filePath = path.join(root, safe);
  if (!filePath.startsWith(path.resolve(root))) {
    return send(res, 403, { error: 'forbidden' });
  }
  let st;
  try {
    st = await fsp.stat(filePath);
    if (st.isDirectory()) throw new Error('is dir');
  } catch {
    return send(res, 404, { error: 'not found', path: safe });
  }
  const ext = path.extname(filePath).toLowerCase();
  res.writeHead(200, {
    'Content-Type': MIME[ext] || 'application/octet-stream',
    'Content-Length': st.size,
    'Cache-Control': 'no-cache',
    'Access-Control-Allow-Origin': '*',
  });
  if (head) return res.end();
  fs.createReadStream(filePath).pipe(res);
}

async function handleApi(req, res, url) {
  const p = url.pathname;

  /* ---------- GET /api/config ---------- */
  if (p === '/api/config' && req.method === 'GET') {
    return send(res, 200, {
      modelId: config.modelId,
      dtype: config.dtype,
      dim: config.dim,
      modelLocalPath: config.modelLocalPath,
      allowRemoteModels: config.allowRemoteModels,
      database: config.database,
      table: config.table,
      db: { host: config.dbHost, port: config.dbPort, user: config.dbUser },
      session: { id: session.id, startedAt: session.startedAt },
      availableModels: await listLocalModels(),
    });
  }

  /* ---------- GET /api/status ---------- */
  if (p === '/api/status' && req.method === 'GET') {
    try {
      const rows = await db.getClient().execute('SELECT VERSION() AS v');
      const docCount = await db.countDocs();
      return send(res, 200, {
        ok: true,
        db: {
          connected: true,
          version: rows?.[0]?.v || 'unknown',
          host: config.dbHost,
          port: config.dbPort,
          database: config.database,
          table: config.table,
          docCount,
        },
        session,
      });
    } catch (e) {
      return send(res, 500, { ok: false, error: String(e.message), session });
    }
  }

  /* ---------- GET /api/docs ---------- */
  if (p === '/api/docs' && req.method === 'GET') {
    const limit = Number(url.searchParams.get('limit') || 100);
    const docs = await db.listDocs(limit);
    return send(res, 200, { ok: true, docs });
  }

  /* ---------- POST /api/import ---------- */
  if (p === '/api/import' && req.method === 'POST') {
    const body = await readBody(req);
    const docs = Array.isArray(body.docs) ? body.docs : [];
    const vectors = Array.isArray(body.vectors) ? body.vectors : [];
    if (!docs.length) return send(res, 400, { ok: false, error: 'docs 为空' });
    if (docs.length !== vectors.length) {
      return send(res, 400, { ok: false, error: 'docs 与 vectors 数量不一致' });
    }
    for (const v of vectors) {
      if (!Array.isArray(v) || v.length !== config.dim) {
        return send(res, 400, { ok: false, error: `向量维度必须为 ${config.dim}` });
      }
    }
    const t0 = performance.now();
    const writeMs = await db.upsertDocs(
      docs.map((d, i) => ({ ...d, vector: vectors[i] }))
    ).then(() => performance.now() - t0);
    const t1 = performance.now();
    const refresh = await db.refreshIndexes();
    const refreshMs = performance.now() - t1;
    const docCount = await db.countDocs();
    session.lastImportAt = new Date().toISOString();
    session.lastDevice = body.device || session.lastDevice;
    session.lastDtype = body.dtype || session.lastDtype;
    session.importedDocs += docs.length;
    return send(res, 200, {
      ok: true,
      inserted: docs.length,
      docCount,
      refresh: refresh.refreshed,
      timings: {
        writeMs: Number(writeMs.toFixed(2)),
        refreshMs: Number(refreshMs.toFixed(2)),
        serverTotalMs: Number((performance.now() - t0).toFixed(2)),
      },
    });
  }

  /* ---------- POST /api/search ---------- */
  if (p === '/api/search' && req.method === 'POST') {
    const body = await readBody(req);
    const qtext = String(body.query || '').trim();
    const qvec = Array.isArray(body.vector) ? body.vector : null;
    const topK = Math.max(1, Math.min(100, Number(body.topK || 10)));
    const mode = body.mode === 'sql' ? 'sql' : 'native';
    if (!qtext && !qvec) return send(res, 400, { ok: false, error: 'query 与 vector 不能同时为空' });
    if (qvec && qvec.length !== config.dim) {
      return send(res, 400, { ok: false, error: `查询向量维度必须为 ${config.dim}` });
    }
    const t0 = performance.now();
    const result = await db.hybridSearch({ qtext, qvec, topK, mode });
    const dbMs = performance.now() - t0;
    session.lastQueryAt = new Date().toISOString();
    session.queryCount += 1;
    return send(res, 200, {
      ok: true,
      mode,
      rows: result.rows.map((r) => {
        const vecRank = r.vec_rank ?? r._semantic_rank ?? null;
        const ftRank = r.ft_rank ?? r._keyword_rank ?? null;
        const hitBy = r.hit_by
          ?? (vecRank !== null && ftRank !== null ? 'both'
            : vecRank !== null ? 'vector'
              : ftRank !== null ? 'fulltext' : null);
        return {
          id: r.id,
          title: r.title,
          snippet: r.snippet ?? r.body?.slice?.(0, 400) ?? '',
          source: r.source,
          updated_at: r.updated_at,
          score: r._score ?? r.rrf_score ?? null,
          vec_rank: vecRank,
          ft_rank: ftRank,
          vec_dist: r.vec_dist ?? null,
          ft_score: r.ft_score ?? r._keyword_score ?? null,
          hit_by: hitBy,
        };
      }),
      timings: { dbMs: Number(dbMs.toFixed(2)), innerMs: Number(result.elapsedMs.toFixed(2)) },
      sql: result.sql,
    });
  }

  /* ---------- POST /api/explain ---------- */
  if (p === '/api/explain' && req.method === 'POST') {
    const body = await readBody(req);
    const qtext = String(body.query || '');
    const qvec = Array.isArray(body.vector) ? body.vector : [];
    const mode = body.mode === 'sql' ? 'sql' : 'native';
    const rows = await db.explainHybrid({ qtext, qvec, topK: Number(body.topK || 10), mode });
    return send(res, 200, { ok: true, plan: rows });
  }

  /* ---------- POST /api/refresh ---------- */
  if (p === '/api/refresh' && req.method === 'POST') {
    const r = await db.refreshIndexes();
    return send(res, 200, { ok: true, ...r });
  }

  /* ---------- POST /api/reset ---------- */
  if (p === '/api/reset' && req.method === 'POST') {
    await db.getClient().execute(`DELETE FROM \`${db.TABLE}\``);
    await db.refreshIndexes();
    return send(res, 200, { ok: true, docCount: await db.countDocs() });
  }

  return send(res, 404, { error: 'unknown api', path: p });
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host || 'localhost'}`);
  try {
    if (req.method === 'OPTIONS') {
      return send(res, 204, '');
    }
    if (url.pathname === '/favicon.ico') {
      res.writeHead(204).end();
      return;
    }
    if (url.pathname.startsWith('/api/')) {
      return await handleApi(req, res, url);
    }
    // 静态资源
    const head = req.method === 'HEAD';
    if (url.pathname.startsWith('/vendor/transformers/')) {
      const rel = url.pathname.slice('/vendor/transformers/'.length);
      return await serveStatic(res, config.vendorDir, rel, { head });
    }
    if (url.pathname.startsWith('/vendor/ort/')) {
      const rel = url.pathname.slice('/vendor/ort/'.length);
      return await serveStatic(res, config.ortDir, rel, { head });
    }
    if (url.pathname.startsWith('/models/')) {
      const rel = url.pathname.slice('/models/'.length);
      return await serveStatic(res, config.modelsDir, rel, { head });
    }
    if (url.pathname.startsWith('/data/')) {
      const rel = url.pathname.slice('/data/'.length);
      return await serveStatic(res, config.dataDir, rel, { head });
    }
    const rel = url.pathname === '/' ? 'index.html' : url.pathname.slice(1);
    return await serveStatic(res, config.publicDir, rel, { head });
  } catch (e) {
    return send(res, 500, { ok: false, error: String(e && e.message) });
  }
});

async function main() {
  console.log('== 连接 seekdb ==');
  await db.connect();
  console.log(`   ${config.dbHost}:${config.dbPort}  database=${config.database} table=${config.table}`);
  await db.ensureSchema();
  console.log('   schema ready');

  server.listen(config.httpPort, config.httpHost, () => {
    console.log(`== 应用已启动 ==  http://${config.httpHost}:${config.httpPort}`);
    console.log(`   模型(浏览器端本地加载): ${config.modelId}  dtype=${config.dtype}`);
    console.log(`   本地模型目录: ${config.modelsDir}`);
  });
}

process.on('SIGINT', async () => { await db.close(); process.exit(0); });
process.on('SIGTERM', async () => { await db.close(); process.exit(0); });

main().catch(async (e) => {
  console.error('启动失败:', e);
  await db.close();
  process.exit(1);
});
