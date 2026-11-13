/**
 * db.js —— seekdb 数据访问层
 *
 * 这里是「技术结合」的落点，所有检索与记忆都在 seekdb 里完成：
 *
 *  · paper_doc       每篇资料的每个段落一行，带**原生全文索引**（ik）与**向量索引**（HNSW/cosine）；
 *  · paper_doc_term  段落级词频（检索统计的事实来源）；
 *  · paper_term      词表与 df（IDF 由数据库统计，不是本地猜的）；
 *  · paper_study     检索足迹（记忆：谁在什么时候问了什么、命中了什么）；
 *  · paper_review    复习计划（记忆：每篇资料的下次复习时间与难度系数）。
 *
 * 混合检索有两条路径，都会把融合交给数据库：
 *   native —— DBMS_HYBRID_SEARCH.GET_SQL() 由数据库生成混合检索 SQL，
 *             全文走 FULLTEXT(ik)，向量走 VECTOR(HNSW) APPROXIMATE，RRF 在库内融合；
 *   sql    —— 手写单条 RRF SQL（等价的降级路径，用于交叉验证与不兼容版本兜底）。
 * 客户端从不分别取两路结果再自己拼合。
 */
import mysql from 'mysql2/promise';

import { config } from './config.js';
import { buildIdf, embed, termFreq, toVectorLiteral } from './embed.js';
import { INITIAL_ROW, nextReview } from './review.js';

let pool = null;

/** 连接池（懒加载） */
export function getPool() {
  if (!pool) {
    pool = mysql.createPool({
      host: config.db.host,
      port: config.db.port,
      user: config.db.user,
      password: config.db.password,
      database: config.db.database,
      connectionLimit: 8,
      charset: 'utf8mb4',
      supportBigNumbers: true,
      dateStrings: true,
    });
  }
  return pool;
}

/** 用一条独立连接执行 SQL */
export async function runSql(sql, params = []) {
  const [rows] = await getPool().execute(sql, params);
  return rows;
}

/** 关闭连接池 */
export async function close() {
  if (pool) {
    await pool.end();
    pool = null;
  }
}

/** 建库（若不存在） */
export async function ensureDatabase() {
  const admin = await mysql.createConnection({
    host: config.db.host,
    port: config.db.port,
    user: config.db.user,
    password: config.db.password,
    multipleStatements: false,
  });
  try {
    await admin.query(
      `CREATE DATABASE IF NOT EXISTS \`${config.db.database}\` DEFAULT CHARSET utf8mb4`,
    );
  } finally {
    await admin.end();
  }
}

/**
 * 建表。
 *
 * 注意：paper_doc **不使用用户主键**——seekdb 的原生混合检索要求表带隐藏的
 * __pk_increment 列；id 上是 UNIQUE 索引，用于幂等 upsert。
 */
export async function ensureSchema() {
  await ensureDatabase();
  await runSql(
    `CREATE TABLE IF NOT EXISTS paper_doc (
       id          VARCHAR(160) NOT NULL,
       title       VARCHAR(256) NOT NULL,
       source      VARCHAR(256) NOT NULL DEFAULT '',
       chunk_index INT NOT NULL DEFAULT 0,
       body        MEDIUMTEXT NOT NULL,
       emb         VECTOR(${config.dim}) DEFAULT NULL,
       updated_at  DATETIME(3) NOT NULL,
       UNIQUE KEY uk_paper_doc_id (id),
       FULLTEXT INDEX ft_paper_doc_body (body) WITH PARSER ik,
       VECTOR INDEX idx_paper_doc_emb (emb) WITH (distance = cosine, type = hnsw, lib = vsag)
     ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4`,
  );
  await runSql(
    `CREATE TABLE IF NOT EXISTS paper_doc_term (
       doc_id VARCHAR(160) NOT NULL,
       term   VARCHAR(96) NOT NULL,
       tf     INT NOT NULL,
       PRIMARY KEY (doc_id, term)
     ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4`,
  );
  await runSql(
    `CREATE TABLE IF NOT EXISTS paper_term (
       term VARCHAR(96) NOT NULL,
       df   INT NOT NULL,
       PRIMARY KEY (term)
     ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4`,
  );
  await runSql(
    `CREATE TABLE IF NOT EXISTS paper_study (
       id         BIGINT AUTO_INCREMENT PRIMARY KEY,
       user_id    VARCHAR(64) NOT NULL,
       doc_id     VARCHAR(160) NOT NULL,
       title      VARCHAR(256) NOT NULL DEFAULT '',
       query_text TEXT,
       hit_by     VARCHAR(16) NOT NULL DEFAULT '',
       score      DOUBLE NOT NULL DEFAULT 0,
       created_at DATETIME NOT NULL,
       KEY idx_paper_study_user (user_id, created_at),
       KEY idx_paper_study_doc (doc_id)
     ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4`,
  );
  await runSql(
    `CREATE TABLE IF NOT EXISTS paper_review (
       user_id       VARCHAR(64) NOT NULL,
       doc_id        VARCHAR(160) NOT NULL,
       title         VARCHAR(256) NOT NULL DEFAULT '',
       ease          DOUBLE NOT NULL DEFAULT 2.5,
       interval_days INT NOT NULL DEFAULT 0,
       reps          INT NOT NULL DEFAULT 0,
       due_at        DATETIME NOT NULL,
       updated_at    DATETIME NOT NULL,
       PRIMARY KEY (user_id, doc_id),
       KEY idx_paper_review_due (user_id, due_at)
     ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4`,
  );
  return true;
}

/** 写入段落（幂等：同一 id 覆盖） */
export async function upsertDoc(doc) {
  const now = new Date().toISOString().slice(0, 23).replace('T', ' ');
  await runSql(
    `INSERT INTO paper_doc (id, title, source, chunk_index, body, updated_at)
     VALUES (?, ?, ?, ?, ?, ?)
     ON DUPLICATE KEY UPDATE
       title = VALUES(title), source = VALUES(source),
       chunk_index = VALUES(chunk_index), body = VALUES(body), updated_at = VALUES(updated_at)`,
    [doc.id, doc.title, doc.source, doc.chunkIndex, doc.body, now],
  );
}

/** 写词频 */
export async function upsertDocTerms(docId, tf) {
  await runSql('DELETE FROM paper_doc_term WHERE doc_id = ?', [docId]);
  const entries = [...tf.entries()];
  if (!entries.length) return;
  const placeholders = entries.map(() => '(?, ?, ?)').join(',');
  const params = [];
  for (const [term, count] of entries) params.push(docId, term, count);
  await runSql(
    `INSERT INTO paper_doc_term (doc_id, term, tf) VALUES ${placeholders}`,
    params,
  );
}

/** 重建词表与 df（纯 SQL 聚合） */
export async function rebuildTermStats() {
  await runSql('DELETE FROM paper_term');
  await runSql(
    `INSERT INTO paper_term (term, df)
     SELECT term, COUNT(*) FROM paper_doc_term GROUP BY term`,
  );
}

/** 读取 df 表 */
export async function loadDf() {
  const rows = await runSql('SELECT term, df FROM paper_term');
  const df = new Map();
  for (const row of rows) df.set(row.term, Number(row.df));
  return df;
}

/** 文档数 */
export async function countDocs() {
  const rows = await runSql('SELECT COUNT(*) AS n FROM paper_doc');
  return Number(rows[0]?.n ?? 0);
}

/**
 * 用当前 IDF 重算所有段落的向量并写回 emb。
 * 语料变大时这一步是 O(段落数)，演示语料规模下毫秒级完成。
 */
export async function reindexVectors() {
  const df = await loadDf();
  const total = await countDocs();
  const idf = buildIdf(df, total);
  const docs = await runSql('SELECT id, title, source, chunk_index, body FROM paper_doc');
  let updated = 0;
  for (const doc of docs) {
    const vector = await embed(`${doc.title}\n${doc.body}`, idf, config.dim);
    await runSql('UPDATE paper_doc SET emb = ? WHERE id = ?', [toVectorLiteral(vector), doc.id]);
    updated += 1;
  }
  return { updated, docCount: total, terms: df.size };
}

/**
 * 完整导入流程：写正文 -> 写词频 -> 重建 df -> 重算向量。
 * @param {Array<{id,title,source,chunkIndex,body}>} docs
 */
export async function ingest(docs) {
  for (const doc of docs) {
    await upsertDoc(doc);
    await upsertDocTerms(doc.id, termFreq(`${doc.title}\n${doc.body}`));
  }
  await rebuildTermStats();
  return await reindexVectors();
}

/* ------------------------------------------------------------------ *
 * 混合检索
 * ------------------------------------------------------------------ */

/** 手写单条 RRF SQL（与原生等价，作为降级与交叉验证路径） */
export const HYBRID_SQL = `
SELECT
  s.id,
  s.title,
  s.source,
  s.chunk_index,
  LEFT(s.body, 400) AS snippet,
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
    id, title, source, chunk_index, body,
    vec_dist, ft_score, vec_rank,
    CASE WHEN ft_score > 0 THEN ROW_NUMBER() OVER (ORDER BY ft_score DESC, id ASC) ELSE NULL END AS ft_rank
  FROM (
    SELECT
      id, title, source, chunk_index, body,
      cosine_distance(emb, ?) AS vec_dist,
      MATCH(body) AGAINST (? IN NATURAL LANGUAGE MODE) AS ft_score,
      ROW_NUMBER() OVER (ORDER BY cosine_distance(emb, ?) ASC, id ASC) AS vec_rank
    FROM paper_doc
  ) vec_candidates
) s
ORDER BY rrf_score DESC, s.vec_rank ASC, s.id ASC
LIMIT ?`;

/** 构造原生混合检索参数 */
export function buildSearchParm({ qtext, qvec, topK, rrfK = 60, rankWindow = 50 }) {
  const parm = {};
  if (qtext) parm.query = { query_string: { fields: ['body'], query: qtext } };
  if (qvec && qvec.length) {
    parm.knn = { field: 'emb', k: Math.max(topK * 2, topK), query_vector: qvec };
  }
  parm.rank = { rrf: { rank_window_size: rankWindow, rank_constant: rrfK } };
  parm.size = topK;
  return parm;
}

/**
 * 原生混合检索的外层 SELECT 只输出融合分数 `_score`，
 * 全文排名 `_keyword_rank` 与向量排名 `_semantic_rank` 留在内层子查询里。
 * 这里在不改变语义的前提下把它们暴露到外层，界面才能告诉用户
 * 「这条是靠全文命中、靠语义命中，还是两路都命中」。
 */
function exposeBranchRanks(sql) {
  const match = /as\s+`?_score`?/.exec(sql);
  if (!match) return sql;
  const insertAt = match.index + match[0].length;
  return (
    sql.slice(0, insertAt) +
    ', _fts._keyword_rank as ft_rank, _vs._semantic_rank as vec_rank' +
    sql.slice(insertAt)
  );
}

/** 原生混合检索：SQL 由数据库生成，融合也在数据库里完成 */
export async function searchNative({ qtext, qvec, topK }) {
  const connection = await getPool().getConnection();
  try {
    const parm = buildSearchParm({ qtext, qvec, topK });
    await connection.query('SET @search_parm = ?', [JSON.stringify(parm)]);
    const [generated] = await connection.query(
      "SELECT DBMS_HYBRID_SEARCH.GET_SQL('paper_doc', @search_parm) AS query_sql FROM dual",
    );
    const raw = String(generated?.[0]?.query_sql || '').trim().replace(/^['"]/, '').replace(/['"]$/, '');
    if (!raw) throw new Error('DBMS_HYBRID_SEARCH returned an empty statement');
    let sql = raw;
    try {
      sql = exposeBranchRanks(raw);
    } catch {
      sql = raw;
    }
    const [rows] = await connection.query(sql);
    return { rows: rows || [], sql, mode: 'native' };
  } finally {
    connection.release();
  }
}

/** 手写 RRF 路径 */
export async function searchSql({ qtext, qvec, topK }) {
  const literal = toVectorLiteral(qvec);
  const rows = await runSql(HYBRID_SQL, [literal, qtext, literal, Number(topK)]);
  return { rows, sql: HYBRID_SQL, mode: 'sql' };
}

/**
 * 把两条检索路径的返回统一成同一形状：
 * id / title / source / chunkIndex / snippet / hitBy / rrfScore / vecRank / ftRank
 */
export function normalizeRows(rows) {
  return (rows || []).map((row) => {
    const vecRank = row.vec_rank ?? row._semantic_rank ?? null;
    const ftRank = row.ft_rank ?? row._keyword_rank ?? null;
    const score = Number(row.rrf_score ?? row._score ?? 0);
    let hitBy = row.hit_by || '';
    if (!hitBy) {
      if (vecRank !== null && ftRank !== null) hitBy = 'both';
      else if (vecRank !== null) hitBy = 'vector';
      else if (ftRank !== null) hitBy = 'fulltext';
      else hitBy = 'vector';
    }
    return {
      id: row.id,
      title: row.title,
      source: row.source,
      chunkIndex: row.chunk_index ?? null,
      snippet: String(row.snippet || row.body || '').slice(0, 400),
      hitBy,
      rrfScore: Number(score.toFixed(8)),
      vecRank: vecRank === null ? null : Number(vecRank),
      ftRank: ftRank === null ? null : Number(ftRank),
    };
  });
}

/**
 * 统一检索入口。
 * @param {{query: string, topK?: number, mode?: 'native'|'sql', userId?: string}} options
 */
export async function search({ query, topK = config.topK, mode = config.searchMode, userId = null }) {
  const text = (query || '').trim();
  if (!text) throw new Error('检索词不能为空');

  const df = await loadDf();
  const idf = buildIdf(df, await countDocs());
  const qvec = await embed(text, idf, config.dim);
  const started = Date.now();

  let result;
  try {
    result = mode === 'sql' ? await searchSql({ qtext: text, qvec, topK }) : await searchNative({ qtext: text, qvec, topK });
  } catch (error) {
    if (mode === 'sql') throw error;
    result = await searchSql({ qtext: text, qvec, topK });
    result.fallbackReason = String(error.message || error);
  }

  const hits = normalizeRows(result.rows);

  // 检索足迹写入记忆表
  if (userId) {
    for (const hit of hits.slice(0, 3)) {
      await runSql(
        `INSERT INTO paper_study (user_id, doc_id, title, query_text, hit_by, score, created_at)
         VALUES (?, ?, ?, ?, ?, ?, NOW())`,
        [userId, hit.id, hit.title || '', text, hit.hitBy || '', hit.rrfScore || 0],
      );
    }
  }
  return { ...result, hits, rows: hits, elapsedMs: Date.now() - started };
}

/**
 * 某个用户最近用过的提问（去重，按最后一次检索倒序）。
 * 数据就是检索足迹表 paper_study，不另建一份前端状态。
 */
export async function recentQueries(userId, limit = 10) {
  const rows = await runSql(
    `SELECT query_text, MAX(id) AS last_id
       FROM paper_study
      WHERE user_id = ? AND query_text IS NOT NULL AND query_text <> ''
      GROUP BY query_text
      ORDER BY last_id DESC
      LIMIT ?`,
    [userId, Number(limit)],
  );
  return rows.map((row) => String(row.query_text));
}

/* ------------------------------------------------------------------ *
 * 复习计划（记忆）
 * ------------------------------------------------------------------ */

/** 取出到期的复习条目 */
export async function listDueReviews(userId, limit = 10) {
  return await runSql(
    `SELECT r.user_id, r.doc_id, r.title, r.ease, r.interval_days, r.reps, r.due_at, r.updated_at,
            d.source, d.chunk_index, LEFT(d.body, 300) AS snippet
       FROM paper_review r
       LEFT JOIN paper_doc d ON d.id = r.doc_id
      WHERE r.user_id = ? AND r.due_at <= NOW()
      ORDER BY r.due_at ASC
      LIMIT ?`,
    [userId, Number(limit)],
  );
}

/** 简单 SM-2：quality 0-5 */
export async function gradeReview(userId, docId, quality, title = '') {
  const rows = await runSql(
    'SELECT ease, interval_days, reps FROM paper_review WHERE user_id = ? AND doc_id = ?',
    [userId, docId],
  );
  const { ease, reps, intervalDays: interval } = nextReview(rows[0] || INITIAL_ROW, quality);
  const dueAt = new Date(Date.now() + interval * 86400000)
    .toISOString()
    .slice(0, 19)
    .replace('T', ' ');
  await runSql(
    `INSERT INTO paper_review (user_id, doc_id, title, ease, interval_days, reps, due_at, updated_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, NOW())
     ON DUPLICATE KEY UPDATE
       title = VALUES(title), ease = VALUES(ease), interval_days = VALUES(interval_days),
       reps = VALUES(reps), due_at = VALUES(due_at), updated_at = VALUES(updated_at)`,
    [userId, docId, title, ease, interval, reps, dueAt],
  );
  return { userId, docId, ease: Number(ease.toFixed(3)), reps, intervalDays: interval, dueAt };
}

/** 把某篇资料加入复习计划（当天到期） */
export async function scheduleReview(userId, docId, title) {
  await runSql(
    `INSERT INTO paper_review (user_id, doc_id, title, ease, interval_days, reps, due_at, updated_at)
     VALUES (?, ?, ?, 2.5, 0, 0, NOW(), NOW())
     ON DUPLICATE KEY UPDATE title = VALUES(title), updated_at = VALUES(updated_at)`,
    [userId, docId, title || ''],
  );
  return { userId, docId };
}

/** 文档列表 */
export async function listDocs(limit = 50) {
  return await runSql(
    `SELECT id, title, source, chunk_index, updated_at, CHAR_LENGTH(body) AS chars
       FROM paper_doc ORDER BY source ASC, chunk_index ASC LIMIT ?`,
    [Number(limit)],
  );
}

/** 概览统计 */
export async function stats() {
  const [docs, terms, studies, reviews] = await Promise.all([
    countDocs(),
    runSql('SELECT COUNT(*) AS n FROM paper_term'),
    runSql('SELECT COUNT(*) AS n FROM paper_study'),
    runSql('SELECT COUNT(*) AS n FROM paper_review'),
  ]);
  return {
    docs,
    terms: Number(terms[0]?.n ?? 0),
    studies: Number(studies[0]?.n ?? 0),
    reviews: Number(reviews[0]?.n ?? 0),
    dim: config.dim,
    mode: config.searchMode,
  };
}
