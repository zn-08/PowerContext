/**
 * scripts/selftest.js — 无浏览器环境下的端到端自测
 *
 * 说明：这是 **等价 Node 侧验证**，用于在没有浏览器的环境里证明四条链路可用：
 *   ① 端侧 embedding 计算（同一套 Transformers.js + 同一份本地模型权重，device=wasm）
 *   ② seekdb 混合检索 SQL（一条同时含全文候选与向量候选）
 *   ③ 增量导入
 *   ④ 索引刷新后新增内容可检索
 *
 * 应用本身（server/ + public/）**不在服务端计算 embedding**，
 * 这里的 Node 侧推理仅作为验证手段，代码与浏览器端完全一致（同一个模型、同一套参数）。
 *
 * 用法： node scripts/selftest.js
 */
import path from 'node:path';
import fs from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { pipeline, env } from '@huggingface/transformers';
import * as db from '../server/db.js';
import { config } from '../server/config.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '..');

const pass = (s) => console.log(`  \x1b[32mPASS\x1b[0m ${s}`);
const fail = (s) => { console.log(`  \x1b[31mFAIL\x1b[0m ${s}`); process.exitCode = 1; };
const info = (s) => console.log(`       ${s}`);

let extractor = null;
let device = 'wasm';
let dtype = 'q8';

async function buildExtractor(dtype) {
  env.allowLocalModels = true;
  env.localModelPath = path.join(ROOT, 'models') + path.sep;
  env.allowRemoteModels = false;
  // 注意：浏览器端的 WASM 后端在 Node 里对应 device='cpu'（onnxruntime-node），
  // 模型权重、量化档位与池化/归一化参数完全一致。
  const candidates = [
    { device: 'cpu', dtype },
    { device: 'cpu', dtype: 'fp32' },
  ];
  for (const c of candidates) {
    try {
      const p = await pipeline('feature-extraction', config.modelId, { device: c.device, dtype: c.dtype });
      device = c.device; dtype = c.dtype;
      return p;
    } catch (e) {
      info(`候选 ${c.device}/${c.dtype} 失败: ${String(e.message).slice(0, 120)}`);
    }
  }
  throw new Error('所有候选后端均不可用');
}

async function embed(texts) {
  const out = await extractor(texts, { pooling: 'mean', normalize: true });
  const dim = out.dims[out.dims.length - 1];
  const data = out.data;
  const vecs = [];
  for (let i = 0; i < out.dims[0]; i++) vecs.push(Array.from(data.slice(i * dim, (i + 1) * dim)));
  return vecs;
}

async function main() {
  console.log('=========================================================');
  console.log(' stepfun-seekdb-hybrid-kb  自测 (Node 侧等价验证)');
  console.log('=========================================================\n');

  /* ---------- 1. seekdb 连接 ---------- */
  console.log('[1] 连接 seekdb');
  await db.connect();
  await db.ensureSchema();
  const version = await db.getClient().execute('SELECT VERSION() AS v');
  pass(`已连接 ${config.dbHost}:${config.dbPort}  version=${version[0].v}`);
  pass(`schema 就绪 database=${config.database} table=${config.table}`);

  /* ---------- 2. 端侧 embedding ---------- */
  console.log('\n[2] 端侧 embedding 计算（Transformers.js, 本地模型）');
  const t0 = performance.now();
  extractor = await buildExtractor(config.dtype);
  const loadMs = performance.now() - t0;
  pass(`模型加载完成 device=${device} dtype=${dtype} 耗时=${loadMs.toFixed(0)} ms`);
  info(`模型目录 ${path.join(ROOT, 'models', config.modelId)}`);

  const corpus = JSON.parse(await fs.readFile(path.join(ROOT, 'data', 'corpus.json'), 'utf8'));
  const t1 = performance.now();
  const docVecs = await embed(corpus.docs.map((d) => `${d.title}\n${d.text}`));
  const embedMs = performance.now() - t1;
  pass(`文档向量化 ${corpus.docs.length} 篇 耗时=${embedMs.toFixed(1)} ms  吞吐=${(corpus.docs.length / (embedMs / 1000)).toFixed(1)} 篇/秒`);
  info(`向量维度=${docVecs[0].length}（建表 VECTOR(${config.dim})）`);

  /* ---------- 3. 写入 ---------- */
  console.log('\n[3] 写入 seekdb（向量 + 原文 + 元数据）');
  await db.getClient().execute(`DELETE FROM \`${db.TABLE}\``);
  const t2 = performance.now();
  await db.upsertDocs(corpus.docs.map((d, i) => ({ ...d, vector: docVecs[i] })));
  const writeMs = performance.now() - t2;
  const n = await db.countDocs();
  pass(`写入 ${n} 篇 耗时=${writeMs.toFixed(1)} ms（${(writeMs / n).toFixed(2)} ms/篇）`);
  const row = await db.getClient().execute(`SELECT id, title, LENGTH(body) AS bl, JSON_EXTRACT(meta,'$.topic') AS topic FROM \`${db.TABLE}\` LIMIT 1`);
  info(`抽样行: ${JSON.stringify(row[0])}`);

  /* ---------- 4. 混合检索 SQL ---------- */
  console.log('\n[4] 混合检索（一条同时含全文候选与向量候选的 SQL）');
  const q = corpus.queries[0];
  const qv = (await embed([q.q]))[0];
  const res = await db.hybridSearch({ qtext: q.q, qvec: qv, topK: 5, mode: 'native' });
  pass(`原生 DBMS_HYBRID_SEARCH 命中 ${res.rows.length} 条，耗时=${res.elapsedMs.toFixed(2)} ms`);
  info(`查询: ${q.q}`);
  for (const r of res.rows) {
    info(`   ${r.id}  score=${r._score}  title=${String(r.title).slice(0, 40)}`);
  }
  const res2 = await db.hybridSearch({ qtext: q.q, qvec: qv, topK: 5, mode: 'sql' });
  pass(`手写单条 RRF SQL 命中 ${res2.rows.length} 条，耗时=${res2.elapsedMs.toFixed(2)} ms`);
  for (const r of res2.rows) {
    info(`   ${r.id}  rrf=${r.rrf_score}  vec_rank=${r.vec_rank} ft_rank=${r.ft_rank} hit_by=${r.hit_by}`);
  }

  /* ---------- 5. 索引计划（证明用了两个索引） ---------- */
  console.log('\n[5] EXPLAIN：确认全文与向量索引都被使用');
  const plan = await db.explainHybrid({ qtext: q.q, qvec: qv, topK: 5, mode: 'native' });
  const planText = (plan || []).map((r) => r['Query Plan'] ?? Object.values(r).join(' ')).join('\n');
  const usesFt = /TEXT RETRIEVAL SCAN/i.test(planText);
  const usesVec = /VECTOR INDEX SCAN/i.test(planText);
  usesFt ? pass('使用 FULLTEXT 索引 (TEXT RETRIEVAL SCAN)') : fail('未使用 FULLTEXT 索引');
  usesVec ? pass('使用 VECTOR 索引 (VECTOR INDEX SCAN)') : fail('未使用 VECTOR 索引');
  for (const l of planText.split('\n').filter((x) => /\|ID\||SCAN/.test(x))) info(`   ${l.trim()}`);

  /* ---------- 6. 增量导入 + 索引刷新 ---------- */
  console.log('\n[6] 增量导入 + 索引刷新');
  const incDoc = {
    id: 'inc_001',
    title: '增量导入的新文档：边缘设备上的向量缓存',
    text: '这是一篇在首次导入之后才写入的增量文档，专门讨论边缘设备上的向量缓存与增量索引刷新机制，用于验证新增内容在刷新后立刻可被检索到。',
    source: 'incremental',
    meta: { topic: 'inc' },
  };
  const incVec = (await embed([`${incDoc.title}\n${incDoc.text}`]))[0];
  await db.upsertDocs([{ ...incDoc, vector: incVec }]);
  const refresh = await db.refreshIndexes();
  const n2 = await db.countDocs();
  pass(`增量写入完成 文档数 ${n} -> ${n2}，索引刷新方式=${refresh.refreshed}`);

  const incRes = await db.hybridSearch({
    qtext: '边缘设备上的向量缓存',
    qvec: incVec,
    topK: 3,
    mode: 'native',
  });
  const found = incRes.rows.some((r) => r.id === 'inc_001');
  found ? pass('增量文档已被检索到（索引刷新生效）') : fail('增量文档未被检索到');
  for (const r of incRes.rows) info(`   ${r.id}  score=${r._score}  title=${String(r.title).slice(0, 44)}`);

  /* ---------- 7. 质量评测 ---------- */
  console.log('\n[7] 检索质量（Recall@K / nDCG@K，人工分级相关性）');
  // 先把集合恢复成干净的演示语料（步骤 6 追加过一篇增量文档），
  // 以便与 scripts/bench.js 的评测口径完全一致。
  await db.getClient().execute(`DELETE FROM \`${db.TABLE}\``);
  await db.refreshIndexes();
  await db.upsertDocs(corpus.docs.map((d, i) => ({ ...d, vector: docVecs[i] })));
  await db.refreshIndexes();
  const K = 5;
  let recallSum = 0, ndcgSum = 0, mrr = 0, evaluated = 0;
  const perQuery = [];
  for (const item of corpus.queries) {
    const qv2 = (await embed([item.q]))[0];
    const r = await db.hybridSearch({ qtext: item.q, qvec: qv2, topK: K, mode: 'native' });
    const ranked = r.rows.map((x) => x.id);
    const rel = item.relevance;
    const totalRel = Object.values(rel).reduce((a, b) => a + b, 0);
    let got = 0, dcg = 0, idcg = 0, firstHit = 0;
    ranked.forEach((id, i) => {
      const g = rel[id] || 0;
      if (g > 0) { got += g; dcg += (2 ** g - 1) / Math.log2(i + 2); if (!firstHit) firstHit = i + 1; }
    });
    const sorted = Object.values(rel).sort((a, b) => b - a);
    for (let i = 0; i < sorted.length; i++) idcg += (2 ** sorted[i] - 1) / Math.log2(i + 2);
    const recall = totalRel ? got / totalRel : 0;
    const ndcg = idcg ? dcg / idcg : 0;
    recallSum += recall; ndcgSum += ndcg; mrr += firstHit ? 1 / firstHit : 0; evaluated += 1;
    perQuery.push({ q: item.q, recall: recall.toFixed(3), ndcg: ndcg.toFixed(3), mrr: (firstHit ? 1 / firstHit : 0).toFixed(3), ranked });
  }
  const avg = (a) => (a / evaluated).toFixed(4);
  info(`queries=${evaluated}  K=${K}`);
  info(`Recall@${K} = ${avg(recallSum)}   nDCG@${K} = ${avg(ndcgSum)}   MRR = ${avg(mrr)}`);
  for (const p of perQuery) info(`   "${p.q}"  R@${K}=${p.recall} nDCG=${p.ndcg} MRR=${p.mrr}`);

  await db.close();
  console.log('\n=========================================================');
  console.log(process.exitCode ? ' 结果：存在失败项' : ' 结果：全部通过');
  console.log('=========================================================');
}

main().catch(async (e) => {
  console.error('自测异常:', e);
  await db.close();
  process.exit(1);
});
