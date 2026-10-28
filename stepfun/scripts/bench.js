/**
 * scripts/bench.js — 性能与质量基准测试
 *
 * 分阶段计时（首次 / 预热后 / 中位数），重复测试取中位数：
 *   ① 模型加载（冷：磁盘读取权重；热：进程内复用）
 *   ② 文档向量化（吞吐 = 篇/秒）
 *   ③ 写入 seekdb + 索引刷新
 *   ④ 查询向量化
 *   ⑤ seekdb 混合检索耗时
 *   ⑥ 端到端（提交查询 -> 结果可展示）
 *
 * 检索质量：Recall@K / nDCG@K / MRR（人工分级相关性，数据集见 data/corpus.json）
 *
 * 用法： node scripts/bench.js            # 完整基准
 *       node scripts/bench.js --quick     # 只跑默认模型
 */
import path from 'node:path';
import fs from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { pipeline, env } from '@huggingface/transformers';
import * as db from '../server/db.js';
import { config, ROOT } from '../server/config.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const QUICK = process.argv.includes('--quick');
const K = 5;

env.allowLocalModels = true;
env.localModelPath = path.join(ROOT, 'models') + path.sep;
env.allowRemoteModels = false;

const stats = (arr) => {
  const s = [...arr].sort((a, b) => a - b);
  const med = s.length % 2 ? s[(s.length - 1) / 2] : (s[s.length / 2 - 1] + s[s.length / 2]) / 2;
  return {
    n: s.length,
    first: s[0],
    min: s[0],
    median: med,
    max: s[s.length - 1],
    mean: s.reduce((a, b) => a + b, 0) / s.length,
  };
};
const f2 = (v) => Number(v).toFixed(2);
const f3 = (v) => Number(v).toFixed(3);

async function loadExtractor(modelId, dtype) {
  const t0 = performance.now();
  const p = await pipeline('feature-extraction', modelId, { device: 'cpu', dtype });
  return { p, ms: performance.now() - t0 };
}

async function embed(ex, texts) {
  const out = await ex(texts, { pooling: 'mean', normalize: true });
  const dim = out.dims[out.dims.length - 1];
  const vecs = [];
  for (let i = 0; i < out.dims[0]; i++) vecs.push(Array.from(out.data.slice(i * dim, (i + 1) * dim)));
  return vecs;
}

async function fileSize(repo, file) {
  try {
    const st = await fs.stat(path.join(ROOT, 'models', repo, file));
    return st.size;
  } catch { return null; }
}

/** 质量评测 */
async function evaluate(ex, corpus, mode) {
  let recall = 0, ndcg = 0, mrr = 0, n = 0;
  const per = [];
  for (const item of corpus.queries) {
    const qv = (await embed(ex, [item.q]))[0];
    const r = await db.hybridSearch({ qtext: item.q, qvec: qv, topK: K, mode });
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
    const rc = totalRel ? got / totalRel : 0;
    const nd = idcg ? dcg / idcg : 0;
    recall += rc; ndcg += nd; mrr += firstHit ? 1 / firstHit : 0; n += 1;
    per.push({ q: item.q, rc: f3(rc), nd: f3(nd) });
  }
  return {
    recall: recall / n, ndcg: ndcg / n, mrr: mrr / n, per,
  };
}

async function main() {
  const corpus = JSON.parse(await fs.readFile(path.join(ROOT, 'data', 'corpus.json'), 'utf8'));
  await db.connect();

  const CONFIGS = [
    { repo: 'Xenova/all-MiniLM-L6-v2', label: 'all-MiniLM-L6-v2 (英文为主, 384d)', dtypes: QUICK ? ['q8'] : ['q8', 'fp16', 'fp32'] },
    { repo: 'Xenova/paraphrase-multilingual-MiniLM-L12-v2', label: 'paraphrase-multilingual-MiniLM-L12-v2 (多语言, 384d)', dtypes: QUICK ? [] : ['q8', 'fp32'] },
  ];

  const report = { generatedAt: new Date().toISOString(), device: 'Node (onnxruntime-node / cpu)', results: [] };
  const outLines = [];

  for (const cfg of CONFIGS) {
    if (!cfg.dtypes.length) continue;
    for (const dtype of cfg.dtypes) {
      const table = `bench_${cfg.repo.split('/')[1].toLowerCase().replace(/[^a-z0-9]/g, '_')}_${dtype}`;
      const onnxFile = { q8: 'onnx/model_quantized.onnx', fp16: 'onnx/model_fp16.onnx', fp32: 'onnx/model.onnx' }[dtype];
      const bytes = await fileSize(cfg.repo, onnxFile);

      outLines.push(`\n${'='.repeat(78)}\n模型: ${cfg.repo}  量化: ${dtype}  onnx 体积: ${bytes ? f2(bytes / 1048576) + ' MB' : 'n/a'}\n${'='.repeat(78)}`);

      // ① 模型加载（冷：本次新建 pipeline）
      const { p: ex, ms: loadMs } = await loadExtractor(cfg.repo, dtype);
      outLines.push(`  ① 模型加载(冷)          : ${f2(loadMs)} ms`);

      // ② 文档向量化：首次 + 预热后中位数
      const docTexts = corpus.docs.map((d) => `${d.title}\n${d.text}`);
      let docVecs = null;
      const docTimes = [];
      for (let i = 0; i < 6; i++) {
        const t0 = performance.now();
        docVecs = await embed(ex, docTexts);
        docTimes.push(performance.now() - t0);
      }
      const ds = stats(docTimes);
      const docThroughput = docTexts.length / (ds.median / 1000);
      outLines.push(`  ② 文档向量化 ${docTexts.length} 篇   : 首次 ${f2(ds.first)} ms | 中位数 ${f2(ds.median)} ms | 吞吐 ${f2(docThroughput)} 篇/秒`);

      // ③ 写入 seekdb + 索引刷新
      await db.getClient().execute(`DELETE FROM \`${db.TABLE}\``);
      await db.refreshIndexes();

      const tW = performance.now();
      await db.upsertDocs(corpus.docs.map((d, i) => ({ ...d, vector: docVecs[i] })));
      const writeMs = performance.now() - tW;
      const tR = performance.now();
      await db.refreshIndexes();
      const refreshMs = performance.now() - tR;
      outLines.push(`  ③ 写入 ${corpus.docs.length} 篇        : ${f2(writeMs)} ms (${f2(writeMs / corpus.docs.length)} ms/篇)`);
      outLines.push(`     索引刷新              : ${f2(refreshMs)} ms`);

      // ④⑤⑥ 查询：向量化 / 检索 / 端到端
      const qEmbed = [];
      const dbSearch = [];
      const e2e = [];
      const qVecs = [];
      for (const item of corpus.queries) {
        const tE0 = performance.now();
        const qv = (await embed(ex, [item.q]))[0];
        qVecs.push(qv);
        qEmbed.push(performance.now() - tE0);
        const tS0 = performance.now();
        await db.hybridSearch({ qtext: item.q, qvec: qv, topK: K, mode: 'native' });
        dbSearch.push(performance.now() - tS0);
        e2e.push(performance.now() - tE0);
      }
      // 预热后再测一轮（数据库连接池/计划缓存已热）
      for (let rep = 0; rep < 5; rep++) {
        for (let i = 0; i < corpus.queries.length; i++) {
          const item = corpus.queries[i];
          const tE0 = performance.now();
          const qv = (await embed(ex, [item.q]))[0];
          qEmbed.push(performance.now() - tE0);
          const tS0 = performance.now();
          await db.hybridSearch({ qtext: item.q, qvec: qv, topK: K, mode: 'native' });
          dbSearch.push(performance.now() - tS0);
          e2e.push(performance.now() - tE0);
        }
      }
      const qe = stats(qEmbed), dbs = stats(dbSearch), e2 = stats(e2e);
      outLines.push(`  ④ 查询向量化            : 首次 ${f2(qe.first)} ms | 中位数 ${f2(qe.median)} ms (n=${qe.n})`);
      outLines.push(`  ⑤ seekdb 混合检索       : 首次 ${f2(dbs.first)} ms | 中位数 ${f2(dbs.median)} ms (n=${dbs.n})`);
      outLines.push(`  ⑥ 端到端(向量化+检索)   : 首次 ${f2(e2.first)} ms | 中位数 ${f2(e2.median)} ms (n=${e2.n})`);

      // 质量
      const q = await evaluate(ex, corpus, 'native');
      outLines.push(`  ⑦ 检索质量 (K=${K})       : Recall@${K}=${f3(q.recall)}  nDCG@${K}=${f3(q.ndcg)}  MRR=${f3(q.mrr)}  (查询数 ${corpus.queries.length})`);

      report.results.push({
        model: cfg.repo, dtype, onnxBytes: bytes,
        loadMs: f2(loadMs),
        docEmbed: { first: f2(ds.first), median: f2(ds.median), throughput: f2(docThroughput) },
        writeMs: f2(writeMs), refreshMs: f2(refreshMs),
        queryEmbedMs: { first: f2(qe.first), median: f2(qe.median) },
        dbSearchMs: { first: f2(dbs.first), median: f2(dbs.median) },
        e2eMs: { first: f2(e2.first), median: f2(e2.median) },
        quality: { recall: f3(q.recall), ndcg: f3(q.ndcg), mrr: f3(q.mrr) },
      });

      // 清理：留空集合，HTTP 环节会重新灌入
      await db.getClient().execute(`DELETE FROM \`${db.TABLE}\``);
      await db.refreshIndexes();
    }
  }

  // 端到端 HTTP 全链路（默认模型，走真实后端服务）
  const base = `http://${config.httpHost}:${config.httpPort}`;
  try {
    const st = await fetch(`${base}/api/status`).then((r) => r.json());
    if (st.ok) {
      outLines.push(`\n${'-'.repeat(78)}\nHTTP 全链路（经由运行中的后端 ${base}）\n${'-'.repeat(78)}`);
      const corpusPost = corpus.docs.map((d) => ({ id: d.id, title: d.title, text: d.text, source: d.source, meta: d.meta }));
      const { p: ex } = await loadExtractor(config.modelId, config.dtype);
      const vecs = await embed(ex, corpus.docs.map((d) => `${d.title}\n${d.text}`));
      await fetch(`${base}/api/reset`, { method: 'POST' });
      const impT0 = performance.now();
      const impR = await fetch(`${base}/api/import`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ docs: corpusPost, vectors: vecs, device: 'node', dtype: config.dtype }),
      }).then((r) => r.json());
      const impTotal = performance.now() - impT0;
      outLines.push(`  HTTP /api/import      : 服务端写入 ${f2(impR.timings.writeMs)} ms + 刷新 ${f2(impR.timings.refreshMs)} ms，总计(含网络与向量化) ${f2(impTotal)} ms，文档数 ${impR.docCount}`);
      const httpE2e = [];
      const httpDb = [];
      for (let rep = 0; rep < 6; rep++) {
        for (const item of corpus.queries) {
          const qv = (await embed(ex, [item.q]))[0];
          const t0 = performance.now();
          const r = await fetch(`${base}/api/search`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query: item.q, vector: qv, topK: K, mode: 'native' }),
          }).then((x) => x.json());
          httpE2e.push(performance.now() - t0);
          httpDb.push(r.timings.dbMs);
        }
      }
      const he = stats(httpE2e), hd = stats(httpDb);
      outLines.push(`  HTTP /api/search      : 服务端检索中位数 ${f2(hd.median)} ms | 端到端(含向量传输+HTTP+检索) 中位数 ${f2(he.median)} ms (n=${he.n})`);
      report.http = { importTotalMs: f2(impTotal), writeMs: f2(impR.timings.writeMs), refreshMs: f2(impR.timings.refreshMs), dbSearchMs: { first: f2(hd.first), median: f2(hd.median) }, e2eMs: { first: f2(he.first), median: f2(he.median) }, n: he.n };
    }
  } catch (e) {
    outLines.push(`\n  [跳过 HTTP 全链路] ${e.message}（请先启动 node server/index.js）`);
  }

  const text = outLines.join('\n');
  console.log(text);
  await fs.writeFile(path.join(ROOT, 'data', 'bench-report.txt'), text + '\n', 'utf8');
  await fs.writeFile(path.join(ROOT, 'data', 'bench-report.json'), JSON.stringify(report, null, 2), 'utf8');
  console.log('\n已写入 data/bench-report.txt 与 data/bench-report.json');
  await db.close();
}

main().catch(async (e) => { console.error(e); await db.close(); process.exit(1); });
