/**
 * scripts/probe-shape.js — 决定最终表结构：DBMS_HYBRID_SEARCH 对主键/唯一键的要求
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
  catch (e) { return { error: String(e.message).slice(0, 220) }; }
}

const FT = 'FULLTEXT INDEX ft_body (body) WITH PARSER ik';
const VI = 'VECTOR INDEX iv (emb) WITH (distance=cosine, type=hnsw, lib=vsag)';

const shapes = {
  // A: VARCHAR 主键 + InnoDB（当前 stepfun_docs 的形态）
  a_varchar_pk: `CREATE TABLE a_varchar_pk (id VARCHAR(64) NOT NULL, title VARCHAR(512), body MEDIUMTEXT, emb VECTOR(${dim}), meta JSON, updated_at DATETIME(3), PRIMARY KEY (id), ${FT}, ${VI}) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4`,
  // B: 无用户主键，靠隐藏 __pk_increment
  b_nopk: `CREATE TABLE b_nopk (id VARCHAR(64) NOT NULL, title VARCHAR(512), body MEDIUMTEXT, emb VECTOR(${dim}), meta JSON, updated_at DATETIME(3), ${FT}, ${VI}) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4`,
  // C: VARBINARY 主键（SDK 形态）
  c_varbin_pk: `CREATE TABLE c_varbin_pk (id VARBINARY(512) PRIMARY KEY NOT NULL, title VARCHAR(512), body MEDIUMTEXT, emb VECTOR(${dim}), meta JSON, updated_at DATETIME(3), ${FT}, ${VI}) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4`,
  // D: 无主键 + UNIQUE 索引
  d_nopk_unique: `CREATE TABLE d_nopk_unique (id VARCHAR(64) NOT NULL, title VARCHAR(512), body MEDIUMTEXT, emb VECTOR(${dim}), meta JSON, updated_at DATETIME(3), UNIQUE KEY uk_id (id), ${FT}, ${VI}) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4`,
};

for (const [name, ddl] of Object.entries(shapes)) {
  await ex(`DROP TABLE IF EXISTS ${name}`);
  const r = await ex(ddl);
  if (r && r.error) { console.log(`\n### ${name} DDL ERROR ${r.error}`); continue; }
  for (let i = 0; i < 30; i++) {
    await ex(`INSERT INTO ${name} (id,title,body,emb,meta,updated_at) VALUES (?,?,?,?,?,NOW(3))`,
      [`id${i}`, `标题${i % 5}`, `正文 ${i} quantization 向量 检索 混合检索`, JSON.stringify(fakeVec(i + 1)), '{}']);
  }
  const parm = {
    query: { query_string: { fields: ['body'], query: 'quantization' } },
    knn: { field: 'emb', k: 5, query_vector: qvec },
    rank: { rrf: { rank_window_size: 10, rank_constant: 60 } },
    size: 5,
  };
  await ex('SET @search_parm = ?', [JSON.stringify(parm)]);
  const gen = await ex(`SELECT DBMS_HYBRID_SEARCH.GET_SQL('${name}', @search_parm) AS query_sql FROM dual`);
  if (gen && gen.error) { console.log(`\n### ${name} GET_SQL ERROR ${gen.error}`); continue; }
  const sql = gen[0].query_sql;
  const res = await ex(sql);
  console.log(`\n### ${name}`);
  if (res && res.error) { console.log(`  EXEC ERROR: ${res.error}`); continue; }
  console.log(`  OK rows=${res.length}  cols=${Object.keys(res[0] || {}).join(',')}`);
  console.log(`  score=${res[0]?._score} id=${res[0]?.id}`);
  const p = await ex('EXPLAIN ' + sql);
  const plan = Array.isArray(p) ? p.map((x) => x['Query Plan']).join('\n') : String(p.error);
  console.log('  PLAN:\n' + plan.split('\n').filter((l) => /\|ID\||SCAN|INDEX/.test(l)).map((l) => '    ' + l).join('\n'));
}

await conn.end();
