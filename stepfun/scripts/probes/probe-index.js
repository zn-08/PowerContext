/**
 * scripts/probe-index.js — 探查 seekdb 何时会真正使用 FULLTEXT / VECTOR 索引
 */
import { connect, ensureSchema, upsertDocs, close, toVectorLiteral } from '../server/db.js';
import mysql from 'mysql2/promise';
import { config } from '../server/config.js';

const dim = 384;
function fakeVec(seed) {
  const v = new Array(dim).fill(0);
  let s = seed >>> 0;
  for (let i = 0; i < dim; i++) {
    s = (Math.imul(s, 1103515245) + 12345) >>> 0;
    v[i] = (s / 4294967296) - 0.5;
  }
  const n = Math.sqrt(v.reduce((a, b) => a + b * b, 0)) || 1;
  return v.map((x) => x / n);
}

const TOPICS = ['向量检索', '模型量化', '浏览器推理', '分布式数据库', '索引优化', '混合搜索', '端侧计算', '神经网络'];
const KW = ['vector', 'quantization', 'webgpu', 'distributed', 'index', 'hybrid', 'edge', 'neural'];

async function main() {
  await connect();
  await ensureSchema();

  console.log('-- 灌入 800 条测试数据 --');
  const N = 800;
  const docs = [];
  for (let i = 0; i < N; i++) {
    const t = i % TOPICS.length;
    docs.push({
      id: `bulk_${String(i).padStart(4, '0')}`,
      title: `${TOPICS[t]} 案例 ${i}`,
      text: `本文讨论 ${TOPICS[t]} 与 ${KW[t]} 的关系，关键词 ${KW[t]}。案例编号 ${i}，涉及向量检索、模型量化、浏览器端推理等主题。`,
      source: 'bulk',
      meta: { i },
      vector: fakeVec(i + 1),
    });
  }
  await upsertDocs(docs);
  console.log('done');

  const conn = await mysql.createConnection({
    host: config.dbHost, port: config.dbPort, user: config.dbUser,
    password: config.dbPassword, database: config.database,
  });

  const qvec = toVectorLiteral(fakeVec(3));
  const cases = [
    ['A1 vector top-k 裸查询', `SELECT id FROM \`stepfun_docs\` ORDER BY cosine_distance(emb, ?) LIMIT 10`, [qvec]],
    ['A2 vector top-k + hint', `SELECT /*+ approx_vector_index */ id FROM \`stepfun_docs\` ORDER BY cosine_distance(emb, ?) LIMIT 10`, [qvec]],
    ['A3 vector top-k + index hint', `SELECT /*+ index(idx_stepfun_docs_emb) */ id FROM \`stepfun_docs\` ORDER BY cosine_distance(emb, ?) LIMIT 10`, [qvec]],
    ['A4 vector top-k 派生表', `SELECT id FROM (SELECT id, cosine_distance(emb, ?) AS d FROM \`stepfun_docs\` ORDER BY d LIMIT 10) x`, [qvec]],
    ['B1 fulltext where', `SELECT id FROM \`stepfun_docs\` WHERE MATCH(title, doc_text) AGAINST (? IN NATURAL LANGUAGE MODE) > 0 LIMIT 10`, ['quantization']],
    ['B2 fulltext where + order', `SELECT id, MATCH(title, doc_text) AGAINST (? IN NATURAL LANGUAGE MODE) s FROM \`stepfun_docs\` WHERE MATCH(title, doc_text) AGAINST (? IN NATURAL LANGUAGE MODE) > 0 ORDER BY s DESC LIMIT 10`, ['quantization', 'quantization']],
    ['B3 fulltext 派生表', `SELECT id FROM (SELECT id, MATCH(title, doc_text) AGAINST(? IN NATURAL LANGUAGE MODE) AS s FROM \`stepfun_docs\` WHERE MATCH(title, doc_text) AGAINST(? IN NATURAL LANGUAGE MODE) > 0) x`, ['quantization', 'quantization']],
  ];

  for (const [label, sql, params] of cases) {
    try {
      const [ex] = await conn.query('EXPLAIN ' + sql, params);
      const plan = ex.map((r) => r['Query Plan']).join('\n');
      const idx = plan.split('\n').filter((l) => /INDEX|SCAN/.test(l)).join('\n');
      console.log(`\n### ${label}`);
      console.log(idx || plan.split('\n').slice(0, 6).join('\n'));
      const t0 = performance.now();
      const [rows] = await conn.query(sql, params);
      console.log(`   rows=${rows.length} elapsed=${(performance.now() - t0).toFixed(2)}ms`);
    } catch (e) {
      console.log(`\n### ${label}\n   ERROR: ${String(e.message).slice(0, 200)}`);
    }
  }

  await conn.end();
  await close();
}
main().catch(async (e) => { console.error(e); await close(); process.exit(1); });
