/**
 * scripts/probe-native2.js — 表结构对 DBMS_HYBRID_SEARCH 的影响 + 全文索引是否真正使用
 */
import mysql from 'mysql2/promise';
import { config } from '../server/config.js';

const conn = await mysql.createConnection({
  host: config.dbHost, port: config.dbPort, user: config.dbUser,
  password: config.dbPassword, database: config.database,
});
const dim = 384;
function fakeVec(seed) {
  const v = new Array(dim).fill(0);
  let s = seed >>> 0;
  for (let i = 0; i < dim; i++) { s = (Math.imul(s, 1103515245) + 12345) >>> 0; v[i] = (s / 4294967296) - 0.5; }
  const n = Math.sqrt(v.reduce((a, b) => a + b * b, 0)) || 1;
  return v.map((x) => x / n);
}
const qvec = fakeVec(3);
const vecLit = JSON.stringify(qvec);

async function ex(sql, params) {
  try {
    const [rows] = await conn.query(sql, params);
    return rows;
  } catch (e) { return { error: String(e.message).slice(0, 160) }; }
}
function planOf(rows) {
  if (!Array.isArray(rows)) return String(rows.error || rows);
  return rows.map((r) => r['Query Plan']).join('\n');
}
function scanLines(p) {
  return String(p).split('\n').filter((l) => /\|ID\||SCAN|INDEX|ROWS/.test(l)).join('\n');
}

const shapes = {
  s1_nopk: `CREATE TABLE s1_nopk (id VARCHAR(64) NOT NULL, title VARCHAR(512), doc_text MEDIUMTEXT, emb VECTOR(${dim}), meta JSON,
      FULLTEXT INDEX ft (title, doc_text) WITH PARSER ik,
      VECTOR INDEX iv (emb) WITH (distance=cosine, type=hnsw, lib=vsag))`,
  s2_heap_pk: `CREATE TABLE s2_heap_pk (id VARBINARY(512) PRIMARY KEY NOT NULL, title VARCHAR(512), doc_text MEDIUMTEXT, emb VECTOR(${dim}), meta JSON,
      FULLTEXT INDEX ft (title, doc_text) WITH PARSER ik,
      VECTOR INDEX iv (emb) WITH (distance=cosine, type=hnsw, lib=vsag)) ORGANIZATION = HEAP`,
  s3_heap_nopk: `CREATE TABLE s3_heap_nopk (id VARCHAR(64) NOT NULL, title VARCHAR(512), doc_text MEDIUMTEXT, emb VECTOR(${dim}), meta JSON,
      FULLTEXT INDEX ft (title, doc_text) WITH PARSER ik,
      VECTOR INDEX iv (emb) WITH (distance=cosine, type=hnsw, lib=vsag)) ORGANIZATION = HEAP`,
  s4_sdk_like: `CREATE TABLE s4_sdk_like (id VARBINARY(512) PRIMARY KEY NOT NULL, document STRING, embedding VECTOR(${dim}), metadata JSON,
      FULLTEXT INDEX idx_fts (document) WITH PARSER ik,
      VECTOR INDEX idx_vec (embedding) WITH(distance=cosine, type=hnsw, lib=vsag)) ORGANIZATION = HEAP`,
};

for (const [name, ddl] of Object.entries(shapes)) {
  await ex(`DROP TABLE IF EXISTS ${name}`);
  const r = await ex(ddl);
  if (r && r.error) { console.log(`\n### ${name} DDL ERROR ${r.error}`); continue; }
  console.log(`\n### ${name} created`);
  // 灌数据
  const rows = [];
  for (let i = 0; i < 60; i++) {
    rows.push([`id${i}`, `主题${i % 5} 标题`, `正文内容 ${i} quantization 向量 检索 混合`, JSON.stringify(fakeVec(i + 1)), JSON.stringify({ i })]);
  }
  for (const row of rows) {
    await ex(`INSERT INTO ${name} (id, ${name === 's4_sdk_like' ? 'document' : 'title'}, ${name === 's4_sdk_like' ? 'document' : 'doc_text'}, ${name === 's4_sdk_like' ? 'embedding' : 'emb'}, ${name === 's4_sdk_like' ? 'metadata' : 'meta'}) VALUES (?,?,?,?,?)`, row);
  }
  // hybrid search
  const parm = {
    query: { query_string: { fields: [name === 's4_sdk_like' ? 'document' : 'title', name === 's4_sdk_like' ? 'document' : 'doc_text'], query: 'quantization' } },
    knn: { field: name === 's4_sdk_like' ? 'embedding' : 'emb', k: 5, query_vector: qvec },
    rank: { rrf: { rank_window_size: 10, rank_constant: 60 } },
    size: 5,
  };
  await ex('SET @search_parm = ?', [JSON.stringify(parm)]);
  const gen = await ex(`SELECT DBMS_HYBRID_SEARCH.GET_SQL('${name}', @search_parm) AS query_sql FROM dual`);
  if (gen && gen.error) { console.log(`  GET_SQL ERROR: ${gen.error}`); continue; }
  const sql = gen[0].query_sql;
  console.log('  generated SQL: ' + String(sql).replace(/\s+/g, ' ').slice(0, 400));
  const res = await ex(sql);
  if (res && res.error) { console.log(`  EXEC ERROR: ${res.error}`); continue; }
  console.log(`  rows=${res.length} first=${JSON.stringify(res[0]).slice(0, 300)}`);
  const p = planOf(await ex('EXPLAIN ' + sql));
  console.log('  PLAN:\n' + scanLines(p).split('\n').map((l) => '    ' + l).join('\n'));
}

console.log('\n===== 全文索引是否使用（现有 stepfun_docs） =====');
const p1 = planOf(await ex(`EXPLAIN SELECT id FROM stepfun_docs WHERE MATCH(title, doc_text) AGAINST ('quantization' IN NATURAL LANGUAGE MODE) > 0`));
console.log(scanLines(p1));
const p2 = planOf(await ex(`EXPLAIN SELECT id FROM stepfun_docs WHERE MATCH(title, doc_text) AGAINST ('quantization' IN NATURAL LANGUAGE MODE) > 0 LIMIT 5`));
console.log(scanLines(p2));

await conn.end();
