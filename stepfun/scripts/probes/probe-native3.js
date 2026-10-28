/**
 * scripts/probe-native3.js — 找到 DBMS_HYBRID_SEARCH 能识别的 FULLTEXT 索引形态
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

async function ex(sql, params) {
  try { const [rows] = await conn.query(sql, params); return rows; }
  catch (e) { return { error: String(e.message).slice(0, 200) }; }
}

const variants = [
  { n: 'v1_default_2col', ft: 'FULLTEXT INDEX ft (title, doc_text)', cols: ['title', 'doc_text'] },
  { n: 'v2_ik_2col', ft: 'FULLTEXT INDEX ft (title, doc_text) WITH PARSER ik', cols: ['title', 'doc_text'] },
  { n: 'v3_default_1col', ft: 'FULLTEXT INDEX ft (doc_text)', cols: ['doc_text'] },
  { n: 'v4_ik_1col', ft: 'FULLTEXT INDEX ft (doc_text) WITH PARSER ik', cols: ['doc_text'] },
  { n: 'v5_space_1col', ft: 'FULLTEXT INDEX ft (doc_text) WITH PARSER space', cols: ['doc_text'] },
  { n: 'v6_ngram_1col', ft: 'FULLTEXT INDEX ft (doc_text) WITH PARSER ngram', cols: ['doc_text'] },
];

for (const v of variants) {
  const t = v.n;
  await ex(`DROP TABLE IF EXISTS ${t}`);
  const ddl = `CREATE TABLE ${t} (
    id VARCHAR(64) NOT NULL,
    title VARCHAR(512),
    doc_text MEDIUMTEXT,
    emb VECTOR(${dim}),
    meta JSON,
    ${v.ft},
    VECTOR INDEX iv (emb) WITH (distance=cosine, type=hnsw, lib=vsag)
  )`;
  const r = await ex(ddl);
  if (r && r.error) { console.log(`\n### ${t} DDL ERROR ${r.error}`); continue; }
  for (let i = 0; i < 40; i++) {
    await ex(`INSERT INTO ${t} (id,title,doc_text,emb,meta) VALUES (?,?,?,?,?)`,
      [`id${i}`, `主题${i % 5} 标题`, `正文 ${i} quantization 向量 检索 混合检索 optimization`, JSON.stringify(fakeVec(i + 1)), '{}']);
  }
  const parm = {
    query: { query_string: { fields: v.cols, query: 'quantization' } },
    knn: { field: 'emb', k: 5, query_vector: qvec },
    rank: { rrf: { rank_window_size: 10, rank_constant: 60 } },
    size: 5,
  };
  await ex('SET @search_parm = ?', [JSON.stringify(parm)]);
  const gen = await ex(`SELECT DBMS_HYBRID_SEARCH.GET_SQL('${t}', @search_parm) AS query_sql FROM dual`);
  if (gen && gen.error) { console.log(`\n### ${t} GET_SQL ERROR ${gen.error}`); continue; }
  const sql = gen[0].query_sql;
  const res = await ex(sql);
  console.log(`\n### ${t}  (${v.ft})`);
  if (res && res.error) { console.log(`  EXEC ERROR: ${res.error}`); continue; }
  console.log(`  OK rows=${res.length}  cols=${Object.keys(res[0] || {}).join(',')}`);
  console.log('  SQL: ' + sql.replace(/\s+/g, ' '));
  if (res[0]) console.log('  row0: ' + JSON.stringify(res[0]).slice(0, 400));
}

await conn.end();
