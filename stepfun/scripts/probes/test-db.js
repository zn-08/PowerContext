/**
 * scripts/test-db.js — 数据库层联调脚本（不含浏览器）
 * 验证：建表 -> 写入 -> 混合检索 SQL -> 增量导入 -> 索引刷新 -> 可检索
 */
import { connect, ensureSchema, upsertDocs, refreshIndexes, countDocs, hybridSearch, explainHybrid, close, HYBRID_SQL, toVectorLiteral } from '../server/db.js';

const dim = 384;

// 简易确定性伪向量（仅用于联调 SQL 链路，真实向量由浏览器端模型生成）
function fakeVec(seed) {
  const v = new Array(dim).fill(0);
  let s = seed;
  for (let i = 0; i < dim; i++) {
    s = (s * 1103515245 + 12345) % 2147483648;
    v[i] = (s / 2147483648) - 0.5;
  }
  const n = Math.sqrt(v.reduce((a, b) => a + b * b, 0)) || 1;
  return v.map((x) => x / n);
}

const DOCS = [
  { id: 'd1', title: 'OceanBase 向量检索', text: 'OceanBase seekdb 支持向量索引与全文索引的混合检索，HNSW 索引加速高维近邻搜索。', source: 'seed', meta: { topic: 'vector' } },
  { id: 'd2', title: '端侧模型量化', text: 'INT8 量化可以把 embedding 模型体积压缩到 FP32 的四分之一，浏览器端 WebGPU 推理速度显著提升。', source: 'seed', meta: { topic: 'quant' } },
  { id: 'd3', title: '浏览器内 WebGPU 推理', text: 'Transformers.js 支持在浏览器中使用 WebGPU 后端运行 embedding 模型，不支持时自动回退到 WASM。', source: 'seed', meta: { topic: 'webgpu' } },
];

async function main() {
  console.log('== 1. 连接 seekdb ==');
  await connect();
  console.log('connected');

  console.log('== 2. 建表 ==');
  await ensureSchema();
  console.log('schema ok');

  console.log('== 3. 写入文档 ==');
  await upsertDocs(DOCS.map((d, i) => ({ ...d, vector: fakeVec(i + 1) })));
  console.log('docs =', await countDocs());

  console.log('== 4. 混合检索 SQL ==');
  const qvec = fakeVec(2);
  const res = await hybridSearch({ qvec, qtext: '向量 检索', topK: 5 });
  console.log('elapsed ms =', res.elapsedMs.toFixed(2));
  console.log(JSON.stringify(res.rows, null, 2));

  console.log('== 5. EXPLAIN ==');
  const ex = await explainHybrid(qvec, '向量');
  console.log(JSON.stringify(ex, null, 2).slice(0, 4000));

  console.log('== 6. 增量导入 + 索引刷新 ==');
  await upsertDocs([{ id: 'd4', title: '新增文档：分布式向量数据库', text: '这是一条增量导入的新文档，讨论分布式向量数据库与索引刷新机制。', source: 'inc', meta: { topic: 'new' }, vector: fakeVec(9) }]);
  console.log('refresh:', JSON.stringify(await refreshIndexes()));
  const n2 = await countDocs();
  console.log('docs after =', n2);
  const res2 = await hybridSearch({ qvec: fakeVec(9), qtext: '增量导入 新增文档', topK: 5 });
  console.log('post-incident rows:', JSON.stringify(res2.rows.map((r) => ({ id: r.id, title: r.title, ft: r.ft_score, rrf: r.rrf_score })), null, 2));

  await close();
}

main().catch(async (e) => {
  console.error('FAILED:', e);
  await close();
  process.exit(1);
});
