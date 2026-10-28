/**
 * scripts/diag-quality.js — 定位检索质量损失来源（向量分支 vs 全文分支 vs 融合）
 */
import path from 'node:path';
import fs from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { pipeline, env } from '@huggingface/transformers';
import * as db from '../server/db.js';
import { config } from '../server/config.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '..');

env.allowLocalModels = true;
env.localModelPath = path.join(ROOT, 'models') + path.sep;
env.allowRemoteModels = false;

const extractor = await pipeline('feature-extraction', config.modelId, { device: 'cpu', dtype: 'q8' });

async function embed(texts) {
  const out = await extractor(texts, { pooling: 'mean', normalize: true });
  const dim = out.dims[out.dims.length - 1];
  const vecs = [];
  for (let i = 0; i < out.dims[0]; i++) vecs.push(Array.from(out.data.slice(i * dim, (i + 1) * dim)));
  return vecs;
}

await db.connect();
await db.ensureSchema();

const corpus = JSON.parse(await fs.readFile(path.join(ROOT, 'data', 'corpus.json'), 'utf8'));
await db.getClient().execute(`DELETE FROM \`${db.TABLE}\``);
const docVecs = await embed(corpus.docs.map((d) => `${d.title}\n${d.text}`));
await db.upsertDocs(corpus.docs.map((d, i) => ({ ...d, vector: docVecs[i] })));

const K = 5;
const stats = { vec: [], ft: [], hybrid: [] };

for (const item of corpus.queries) {
  const qv = (await embed([item.q]))[0];
  const rel = item.relevance;
  const totalRel = Object.values(rel).reduce((a, b) => a + b, 0);

  const vecRows = await db.vectorOnlySearch({ qvec: qv, topK: K });
  const ftRows = await db.fulltextOnlySearch({ qtext: item.q, topK: K });
  const hyb = await db.hybridSearch({ qtext: item.q, qvec: qv, topK: K, mode: 'native' });

  const r = (ids) => {
    let got = 0;
    for (const id of ids) got += rel[id] || 0;
    return totalRel ? got / totalRel : 0;
  };
  stats.vec.push(r(vecRows.map((x) => x.id)));
  stats.ft.push(r(ftRows.map((x) => x.id)));
  stats.hybrid.push(r(hyb.rows.map((x) => x.id)));

  console.log(`\n"${item.q}"  (期望: ${Object.keys(rel).join(',')})`);
  console.log(`  向量 top${K}: ${vecRows.map((x) => x.id).join(',')}  R=${stats.vec.at(-1).toFixed(2)}`);
  console.log(`  全文 top${K}: ${ftRows.map((x) => x.id).join(',')}  R=${stats.ft.at(-1).toFixed(2)}`);
  console.log(`  混合 top${K}: ${hyb.rows.map((x) => x.id).join(',')}  R=${stats.hybrid.at(-1).toFixed(2)}`);
  if (ftRows.length === 0) console.log('  !! 全文分支 0 命中');
}

const avg = (a) => (a.reduce((x, y) => x + y, 0) / a.length).toFixed(3);
console.log('\n===== 平均 Recall@5 =====');
console.log(`  仅向量: ${avg(stats.vec)}`);
console.log(`  仅全文: ${avg(stats.ft)}`);
console.log(`  混合  : ${avg(stats.hybrid)}`);

// 全文分词诊断：看 ik 分词后的命中情况
console.log('\n===== 全文分词诊断（ik smart） =====');
for (const item of corpus.queries.slice(0, 5)) {
  const rows = await db.getClient().execute(
    `SELECT id, MATCH(body) AGAINST (? IN NATURAL LANGUAGE MODE) AS s FROM \`${db.TABLE}\` ORDER BY s DESC LIMIT 3`,
    [item.q]
  );
  console.log(`  "${item.q}" -> ${rows.map((r) => `${r.id}:${Number(r.s).toFixed(3)}`).join(' ')}`);
}

await db.close();
