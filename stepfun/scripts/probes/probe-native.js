/**
 * scripts/probe-native.js — 验证 OceanBase 原生混合检索 DBMS_HYBRID_SEARCH 与 APPROXIMATE 索引
 */
import mysql from 'mysql2/promise';
import { config } from '../server/config.js';

const conn = await mysql.createConnection({
  host: config.dbHost, port: config.dbPort, user: config.dbUser,
  password: config.dbPassword, database: config.database,
});
const T = 'stepfun_docs';
const dim = 384;
function fakeVec(seed) {
  const v = new Array(dim).fill(0);
  let s = seed >>> 0;
  for (let i = 0; i < dim; i++) { s = (Math.imul(s, 1103515245) + 12345) >>> 0; v[i] = (s / 4294967296) - 0.5; }
  const n = Math.sqrt(v.reduce((a, b) => a + b * b, 0)) || 1;
  return v.map((x) => x / n);
}
const qvec = fakeVec(3);

async function tryExec(label, sql, params) {
  try {
    const [rows] = await conn.query(sql, params);
    console.log(`\n### ${label}  -> ${rows.length} rows`);
    console.log(JSON.stringify(rows).slice(0, 1800));
    return rows;
  } catch (e) {
    console.log(`\n### ${label}  -> ERROR ${String(e.message).slice(0, 300)}`);
    return null;
  }
}

console.log('===== 1. APPROXIMATE 关键字 =====');
await tryExec('A1 无 APPROXIMATE', `SELECT id FROM \`${T}\` ORDER BY cosine_distance(emb, ?) LIMIT 10`, [JSON.stringify(qvec)]);
await tryExec('A2 带 APPROXIMATE', `SELECT id, cosine_distance(emb, ?) AS d FROM \`${T}\` ORDER BY cosine_distance(emb, ?) APPROXIMATE LIMIT 10`, [JSON.stringify(qvec), JSON.stringify(qvec)]);

const [ex] = await conn.query(`EXPLAIN SELECT id, cosine_distance(emb, ?) AS d FROM \`${T}\` ORDER BY cosine_distance(emb, ?) APPROXIMATE LIMIT 10`, [JSON.stringify(qvec), JSON.stringify(qvec)]);
console.log('\n### EXPLAIN APPROXIMATE');
console.log(ex.map((r) => r['Query Plan']).join('\n').split('\n').filter((l) => /ID|INDEX|SCAN|ROWS/.test(l)).join('\n'));

console.log('\n===== 2. DBMS_HYBRID_SEARCH =====');
const searchParm = {
  query: { query_string: { fields: ['title', 'doc_text'], query: '量化' } },
  knn: { field: 'emb', k: 10, query_vector: qvec },
  rank: { rrf: { rank_window_size: 10, rank_constant: 60 } },
  size: 10,
};
await tryExec('2a SET @search_parm', 'SET @search_parm = ?', [JSON.stringify(searchParm)]);
const gen = await tryExec('2b GET_SQL', `SELECT DBMS_HYBRID_SEARCH.GET_SQL('${T}', @search_parm) AS query_sql FROM dual`);
if (gen && gen[0] && gen[0].query_sql) {
  const sql = gen[0].query_sql;
  console.log('\n>>> 生成的混合检索 SQL:\n' + sql);
  await tryExec('2c 执行生成的 SQL', sql);
  const [ex2] = await conn.query('EXPLAIN ' + sql);
  console.log('\n### EXPLAIN 生成的混合 SQL');
  console.log(ex2.map((r) => r['Query Plan']).join('\n').split('\n').filter((l) => /ID|INDEX|SCAN|ROWS|MATCH/.test(l)).join('\n'));
}

console.log('\n===== 3. 英文查询词 =====');
const sp2 = { ...searchParm, query: { query_string: { fields: ['title', 'doc_text'], query: 'quantization' } } };
await conn.query('SET @search_parm = ?', [JSON.stringify(sp2)]);
const gen2 = await tryExec('3a GET_SQL(en)', `SELECT DBMS_HYBRID_SEARCH.GET_SQL('${T}', @search_parm) AS query_sql FROM dual`);
if (gen2 && gen2[0] && gen2[0].query_sql) {
  await tryExec('3b 执行(en)', gen2[0].query_sql);
}

await conn.end();
