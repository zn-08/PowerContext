/**
 * server/db.js — seekdb 数据访问层
 *
 * 接入方式：官方 npm SDK `seekdb`（Server 模式，底层 mysql2，Windows 可用）
 *   import { SeekdbClient, SeekdbAdminClient } from 'seekdb';
 *   const client = new SeekdbClient({ host, port, user, password, database });
 *   await client.execute(sql, params);   // 原生 SQL 执行
 *
 * 混合检索的两种实现（都在 seekdb 内部完成融合，客户端不拼合）：
 *   1) native —— OceanBase 原生 DBMS_HYBRID_SEARCH（默认）
 *      由数据库生成「一条同时含全文候选与向量候选的 SQL」，RRF 融合；
 *      全文走 FULLTEXT(ik) 索引，向量走 VECTOR(HNSW) 索引（APPROXIMATE 近似扫描）。
 *   2) sql —— 手写单条 RRF SQL（等价实现，用于交叉验证与降级）
 */
import { SeekdbClient, SeekdbAdminClient } from 'seekdb';
import mysql from 'mysql2/promise';
import { config } from './config.js';

export const TABLE = config.table;
export const DB_NAME = config.database;

let client = null;       // seekdb SDK client
let nativePool = null;   // 专用连接池（原生混合检索需要会话变量 @search_parm 的连接亲和性）

/** 逗号分隔的小数向量 -> seekdb VECTOR 字面量 */
export function toVectorLiteral(vec) {
  return `[${vec.map((v) => (Number.isFinite(v) ? v : 0).toFixed(6)).join(',')}]`;
}

/** 建立连接（Server 模式），必要时创建数据库 */
export async function connect() {
  if (client) return client;

  const admin = new SeekdbAdminClient({
    host: config.dbHost,
    port: config.dbPort,
    user: config.dbUser,
    password: config.dbPassword,
  });
  try {
    await admin.createDatabase(DB_NAME);
  } catch {
    // 数据库已存在时忽略
  } finally {
    try { await admin.close(); } catch { /* ignore */ }
  }

  client = new SeekdbClient({
    host: config.dbHost,
    port: config.dbPort,
    user: config.dbUser,
    password: config.dbPassword,
    database: DB_NAME,
  });
  await client.execute('SELECT 1');

  nativePool = mysql.createPool({
    host: config.dbHost,
    port: config.dbPort,
    user: config.dbUser,
    password: config.dbPassword,
    database: DB_NAME,
    connectionLimit: 4,
    charset: 'utf8mb4',
  });
  return client;
}

export function getClient() {
  if (!client) throw new Error('seekdb 尚未连接，请先调用 connect()');
  return client;
}

/**
 * 建表：向量 + 原文 + 元数据，全部落在 seekdb。
 *
 * 表结构要点（为 OceanBase 原生混合检索 DBMS_HYBRID_SEARCH 适配）：
 *  - **不使用用户自定义主键**（原生混合检索要求表使用隐藏 __pk_increment 列）
 *  - id 上建 UNIQUE 索引以支持幂等 upsert / 增量导入
 *  - body 为全文索引列（title + 原文拼接），单列 FULLTEXT + ik 分词
 *  - emb 为 VECTOR(384) 列，建 HNSW 向量索引（distance=cosine）
 */
export async function ensureSchema() {
  const c = getClient();

  // 若旧表使用了用户主键，迁移为新结构
  const existed = await c.execute(`SHOW TABLES LIKE '${TABLE}'`);
  if (existed && existed.length) {
    const ddl = await c.execute(`SHOW CREATE TABLE \`${TABLE}\``);
    const ddlText = String(ddl?.[0]?.['Create Table'] || '');
    if (/PRIMARY KEY/i.test(ddlText)) {
      console.warn(`[db] 检测到旧表使用用户主键，迁移到无主键结构（__pk_increment）`);
      await c.execute(`DROP TABLE \`${TABLE}\``);
    }
  }

  await c.execute(`CREATE TABLE IF NOT EXISTS \`${TABLE}\` (
    id         VARCHAR(64)   NOT NULL,
    title      VARCHAR(512)  NOT NULL,
    body       MEDIUMTEXT    NOT NULL,
    source     VARCHAR(256)  NOT NULL DEFAULT '',
    meta       JSON          DEFAULT NULL,
    emb        VECTOR(${config.dim}) DEFAULT NULL,
    updated_at DATETIME(3)   NOT NULL,
    UNIQUE KEY uk_${TABLE}_id (id),
    FULLTEXT INDEX ft_${TABLE}_body (body) WITH PARSER ik,
    VECTOR INDEX idx_${TABLE}_emb (emb) WITH (distance = cosine, type = hnsw, lib = vsag)
  ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4`);

  return true;
}

/** 写入文档（含向量）。同一 id 覆盖写入（幂等，支持增量导入）。 */
export async function upsertDocs(docs) {
  const c = getClient();
  let n = 0;
  const now = new Date().toISOString().slice(0, 23).replace('T', ' ');
  for (const d of docs) {
    const vec = d.vector && d.vector.length ? toVectorLiteral(d.vector) : null;
    const body = `${d.title || d.id}\n${d.text}`;
    await c.execute(
      `INSERT INTO \`${TABLE}\`
         (id, title, body, source, meta, emb, updated_at)
       VALUES (?, ?, ?, ?, CAST(? AS JSON), ?, ?)
       ON DUPLICATE KEY UPDATE
         title = VALUES(title), body = VALUES(body), source = VALUES(source),
         meta = VALUES(meta), emb = VALUES(emb), updated_at = VALUES(updated_at)`,
      [
        d.id,
        d.title || d.id,
        body,
        d.source || '',
        JSON.stringify(d.meta || {}),
        vec,
        now,
      ]
    );
    n += 1;
  }
  return n;
}

/**
 * 索引刷新：增量导入后让全文/向量索引立即对新内容可见。
 * OceanBase seekdb 的 FULLTEXT 索引在事务提交后生效，这里显式 COMMIT。
 */
export async function refreshIndexes() {
  const c = getClient();
  await c.execute('COMMIT').catch(() => {});
  const attempts = [
    `ALTER TABLE \`${TABLE}\` REFRESH FULLTEXT INDEX`,
    `ALTER TABLE \`${TABLE}\` REFRESH INDEX ft_${TABLE}_body`,
    `ALTER TABLE \`${TABLE}\` REFRESH INDEX`,
  ];
  let refreshed = 'commit';
  for (const sql of attempts) {
    try {
      await c.execute(sql);
      refreshed = sql;
      break;
    } catch {
      /* 该版本不支持此刷新语法，继续尝试下一种 */
    }
  }
  return { refreshed, docCount: await countDocs() };
}

export async function countDocs() {
  const c = getClient();
  const rows = await c.execute(`SELECT COUNT(*) AS n FROM \`${TABLE}\``);
  return Number(rows?.[0]?.n ?? 0);
}

export async function listDocs(limit = 50) {
  const c = getClient();
  return await c.execute(
    `SELECT id, title, source, updated_at FROM \`${TABLE}\` ORDER BY updated_at DESC, id ASC LIMIT ${Number(limit)}`
  );
}

/* ------------------------------------------------------------------ *
 * 模式 1（默认）：OceanBase 原生混合检索 DBMS_HYBRID_SEARCH
 * ------------------------------------------------------------------ */

/** 构造 search_parm（DBMS_HYBRID_SEARCH 的查询参数） */
export function buildSearchParm({ qtext, qvec, topK = 10, knnK = null, rrfK = 60, rankWindow = 50 }) {
  const parm = {};
  if (qtext) {
    parm.query = { query_string: { fields: ['body'], query: qtext } };
  }
  if (qvec && qvec.length) {
    parm.knn = { field: 'emb', k: knnK || Math.max(topK * 2, topK), query_vector: qvec };
  }
  parm.rank = { rrf: { rank_window_size: rankWindow, rank_constant: rrfK } };
  parm.size = topK;
  return parm;
}

/**
 * DBMS_HYBRID_SEARCH 生成的 SQL 外层只输出融合分数 `_score`，
 * 全文排名 `_keyword_rank` 与向量排名 `_semantic_rank` 留在内层子查询里。
 * 这里在**不改变语义**的前提下，往外层 SELECT 列表里追加这两列，
 * 方便界面展示「命中方式（全文 / 向量 / 两者）」。若注入失败则原样执行。
 */
function exposeBranchRanks(sql) {
  // 外层列名为 `as `_score``（带反引号）
  const m = /as\s+`?_score`?/.exec(sql);
  if (!m) return sql;
  const insertAt = m.index + m[0].length;
  return (
    sql.slice(0, insertAt) +
    ', _fts._keyword_rank as ft_rank, _vs._semantic_rank as vec_rank' +
    sql.slice(insertAt)
  );
}

/**
 * 原生混合检索。
 *
 * 流程（全部在 seekdb 内完成）：
 *   1) SET @search_parm = '<json>'                       —— 会话变量
 *   2) SELECT DBMS_HYBRID_SEARCH.GET_SQL('table', @search_parm)  —— 由数据库生成混合检索 SQL
 *   3) 执行第 2 步返回的那条 SQL —— 一条同时含：
 *        · 全文候选：match(`body`) against('...' in natural language mode)  → 走 FULLTEXT(ik) 索引
 *        · 向量候选：ORDER BY `_distance` APPROXIMATE LIMIT k              → 走 VECTOR(HNSW) 索引
 *        · 融合：RANK() 排名 + RRF (1/(rank+60)) 融合，全部由数据库完成
 *
 * @returns {{ rows: Array, sql: string, elapsedMs: number }}
 */
export async function hybridSearchNative({ qtext, qvec, topK = 10, rrfK = 60 }) {
  if (!nativePool) throw new Error('尚未连接');
  const conn = await nativePool.getConnection();
  const t0 = performance.now();
  try {
    const parm = buildSearchParm({ qtext, qvec, topK });
    await conn.query('SET @search_parm = ?', [JSON.stringify(parm)]);
    const [genRows] = await conn.query(
      `SELECT DBMS_HYBRID_SEARCH.GET_SQL('${TABLE}', @search_parm) AS query_sql FROM dual`
    );
    const rawSql = genRows?.[0]?.query_sql;
    if (!rawSql) return { rows: [], sql: '', elapsedMs: performance.now() - t0 };
    // DBMS_HYBRID_SEARCH 返回的可能是带引号的字符串
    const sql = exposeBranchRanks(String(rawSql).trim().replace(/^['"]/, '').replace(/['"]$/, ''));
    const [rows] = await conn.query(sql);
    const elapsed = performance.now() - t0;
    return { rows: rows || [], sql, elapsedMs: elapsed, searchParm: parm };
  } finally {
    conn.release();
  }
}

/* ------------------------------------------------------------------ *
 * 模式 2：手写单条 RRF 混合检索 SQL（与原生等价，用于交叉验证）
 * ------------------------------------------------------------------ */

export const HYBRID_SQL = `
SELECT
  s.id,
  s.title,
  LEFT(s.body, 400) AS snippet,
  s.source,
  s.updated_at,
  s.vec_dist,
  s.vec_rank,
  s.ft_score,
  s.ft_rank,
  ROUND(1.0 / (60 + s.vec_rank) + COALESCE(1.0 / (60 + s.ft_rank), 0), 8) AS rrf_score,
  CASE WHEN s.vec_rank IS NOT NULL AND s.ft_rank IS NOT NULL THEN 'both'
       WHEN s.vec_rank IS NOT NULL THEN 'vector'
       ELSE 'fulltext' END AS hit_by
FROM (
  SELECT
    id, title, body, source, updated_at,
    vec_dist, ft_score, vec_rank,
    CASE WHEN ft_score > 0 THEN ROW_NUMBER() OVER (ORDER BY ft_score DESC, id ASC) ELSE NULL END AS ft_rank
  FROM (
    SELECT
      id, title, body, source, updated_at,
      cosine_distance(emb, ?) AS vec_dist,
      MATCH(body) AGAINST (? IN NATURAL LANGUAGE MODE) AS ft_score,
      ROW_NUMBER() OVER (ORDER BY cosine_distance(emb, ?) ASC, id ASC) AS vec_rank
    FROM \`${TABLE}\`
  ) vec_candidates
) s
ORDER BY rrf_score DESC, s.vec_rank ASC, s.updated_at DESC
LIMIT ?`;

export async function hybridSearchSql({ qtext, qvec, topK = 10 }) {
  const c = getClient();
  const vec = toVectorLiteral(qvec);
  const t0 = performance.now();
  const rows = await c.execute(HYBRID_SQL, [vec, qtext, vec, Number(topK)]);
  return { rows: rows || [], sql: HYBRID_SQL, elapsedMs: performance.now() - t0 };
}

/** 统一的混合检索入口：mode = 'native' | 'sql' */
export async function hybridSearch({ qtext, qvec, topK = 10, mode = 'native' }) {
  if (mode === 'sql') return hybridSearchSql({ qtext, qvec, topK });
  return hybridSearchNative({ qtext, qvec, topK });
}

/* ------------------------------------------------------------------ *
 * 诊断
 * ------------------------------------------------------------------ */

/** EXPLAIN：证明混合检索 SQL 同时使用 FULLTEXT 索引与 VECTOR 索引 */
export async function explainHybrid({ qtext, qvec, topK = 10, mode = 'native' }) {
  let sql;
  if (mode === 'sql') {
    const vec = toVectorLiteral(qvec);
    sql = `EXPLAIN ${HYBRID_SQL.trim()}`;
    const conn = await mysql.createConnection({
      host: config.dbHost, port: config.dbPort, user: config.dbUser,
      password: config.dbPassword, database: DB_NAME,
    });
    try {
      const [rows] = await conn.query(sql, [vec, qtext, vec, topK]);
      return rows;
    } finally { await conn.end(); }
  }
  const conn = await nativePool.getConnection();
  try {
    const parm = buildSearchParm({ qtext, qvec, topK });
    await conn.query('SET @search_parm = ?', [JSON.stringify(parm)]);
    const [genRows] = await conn.query(
      `SELECT DBMS_HYBRID_SEARCH.GET_SQL('${TABLE}', @search_parm) AS query_sql FROM dual`
    );
    const sql = String(genRows?.[0]?.query_sql || '').trim().replace(/^['"]/, '').replace(/['"]$/, '');
    const [rows] = await conn.query(`EXPLAIN ${exposeBranchRanks(sql)}`);
    return rows;
  } finally { conn.release(); }
}

/** 消融实验：仅向量 */
export async function vectorOnlySearch({ qvec, topK = 10 }) {
  const c = getClient();
  const vec = toVectorLiteral(qvec);
  const rows = await c.execute(
    `SELECT id, title, cosine_distance(emb, ?) AS vec_dist
     FROM \`${TABLE}\` ORDER BY vec_dist APPROXIMATE LIMIT ?`,
    [vec, Number(topK)]
  );
  return rows || [];
}

/** 消融实验：仅全文 */
export async function fulltextOnlySearch({ qtext, topK = 10 }) {
  const c = getClient();
  const rows = await c.execute(
    `SELECT id, title, MATCH(body) AGAINST (? IN NATURAL LANGUAGE MODE) AS ft_score
     FROM \`${TABLE}\`
     WHERE MATCH(body) AGAINST (? IN NATURAL LANGUAGE MODE) > 0
     ORDER BY ft_score DESC LIMIT ?`,
    [qtext, qtext, Number(topK)]
  );
  return rows || [];
}

export async function close() {
  if (client) {
    try { await client.close(); } catch { /* ignore */ }
    client = null;
  }
  if (nativePool) {
    try { await nativePool.end(); } catch { /* ignore */ }
    nativePool = null;
  }
}
