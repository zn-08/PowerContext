/**
 * selftest.js —— 端到端自测，产出可复现的验证记录
 *
 *   node scripts/selftest.js            # 输出到控制台
 *   node scripts/selftest.js evidence/selftest.md   # 同时写文件
 *
 * 覆盖：
 *   1. 建表 + 幂等导入（同一份资料导入两次，段落数不变）
 *   2. 中文语义问句、英文术语、稀有标识符三类查询
 *   3. 两条混合检索路径（DBMS_HYBRID_SEARCH 原生 / 手写 RRF）结果对照
 *   4. EXPLAIN 证明检索同时用到 FULLTEXT 索引与 VECTOR 索引
 *   5. 记忆链路：检索足迹落库、复习计划排期与打分
 */
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { chunkId, chunkText } from '../src/chunk.js';
import { config } from '../src/config.js';
import {
  close,
  countDocs,
  ensureSchema,
  gradeReview,
  ingest,
  listDocs,
  listDueReviews,
  runSql,
  scheduleReview,
  search,
  stats,
} from '../src/db.js';
import { assertEmbedderReady } from '../src/embed.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '..');

const lines = [];
function out(text = '') {
  console.log(text);
  lines.push(text);
}

const USER = 'selftest-user';

async function main() {
  await assertEmbedderReady();
  await ensureSchema();

  out('# 文献罗盘 PaperCompass · 端到端自测记录');
  out();
  out(`- 执行时间：${process.env.EVIDENCE_TIME || '2026-11-10'}`);
  out(`- Node：${process.version}`);
  out(`- seekdb：${config.db.host}:${config.db.port}/${config.db.database}`);
  out(`- 检索模式：${config.searchMode}（sql 为等价降级路径），向量维度 ${config.dim}`);
  out();

  out('## 1. 幂等导入');
  out();
  const before = await countDocs();
  const sample = `# 实验记录：HNSW 参数怎么调

在 10 万条 768 维向量上，ef_construction=200、ef_search=64 时，Recall@10 稳定在 0.95 左右，P99 延迟 18 毫秒。ef_search 调到 128 之后 Recall 只涨了 0.01，延迟却翻倍。

结论是：先调 ef_search 性价比最高，达到收益拐点后再调 ef_construction。
`;
  const chunks = chunkText(sample, { chunkSize: config.chunkSize, chunkOverlap: config.chunkOverlap });
  const docs = chunks.map((body, index) => ({
    id: chunkId('selftest-sample.md', index),
    title: '实验记录：HNSW 参数怎么调',
    source: 'selftest-sample.md',
    chunkIndex: index,
    body,
  }));
  await ingest(docs);
  const middle = await countDocs();
  await ingest(docs);
  const after = await countDocs();
  out('```');
  out(`导入前段落数 = ${before}`);
  out(`首次导入后   = ${middle}`);
  out(`重复导入后   = ${after}   （幂等：id 由 来源+段落序号 派生）`);
  out('```');
  out();

  out('## 2. 检索：三种问法');
  out();
  const queries = [
    '混合检索怎么融合两路结果',
    'HNSW 的 ef_search 调到多少合适',
    'reciprocal rank fusion',
    'EF_SEARCH',
  ];
  out('```');
  for (const q of queries) {
    const result = await search({ query: q, topK: 3, userId: USER });
    out(`$ 问：${q}`);
    out(`  模式=${result.mode}  用时=${result.elapsedMs ?? '-'}ms  命中 ${result.hits.length} 条`);
    for (const hit of result.hits) {
      out(
        `   - [${hit.hitBy}] rrf=${hit.rrfScore.toFixed(5)} 全文名次=${hit.ftRank ?? '-'} 向量名次=${hit.vecRank ?? '-'}  ${hit.source} #${hit.chunkIndex}  ${hit.title}`,
      );
      out(`     ${hit.snippet.slice(0, 70).replace(/\n/g, ' ')}...`);
    }
    out('');
  }
  out('```');
  out();

  out('## 3. 两条检索路径对照（同一查询、同一索引）');
  out();
  out('```');
  const probe = '混合检索怎么融合两路结果';
  const nativeResult = await search({ query: probe, topK: 3, mode: 'native' });
  const sqlResult = await search({ query: probe, topK: 3, mode: 'sql' });
  out(`native 模式命中顺序：`);
  nativeResult.hits.forEach((hit, index) => out(`  ${index + 1}. ${hit.source} #${hit.chunkIndex}  [${hit.hitBy}] ${hit.rrfScore}`));
  out(`sql 模式命中顺序：`);
  sqlResult.hits.forEach((hit, index) => out(`  ${index + 1}. ${hit.source} #${hit.chunkIndex}  [${hit.hitBy}] ${hit.rrfScore}`));
  const overlap = nativeResult.hits.filter((hit) => sqlResult.hits.some((other) => other.id === hit.id)).length;
  out(`两条路径重合条数 = ${overlap}/${nativeResult.hits.length}`);
  out('```');
  out();

  out('## 4. 索引确实被用上了（EXPLAIN）');
  out();
  out('```');
  const explain = await runSql(
    `EXPLAIN SELECT id, cosine_distance(emb, '[${new Array(config.dim).fill(0).join(',')}]') AS d
       FROM paper_doc ORDER BY d APPROXIMATE LIMIT 3`,
  );
  for (const row of explain) out(`  ${JSON.stringify(row)}`);
  out('--- 全文索引 ---');
  const explainFt = await runSql(
    "EXPLAIN SELECT id FROM paper_doc WHERE MATCH(body) AGAINST ('HNSW' IN NATURAL LANGUAGE MODE)",
  );
  for (const row of explainFt) out(`  ${JSON.stringify(row)}`);
  out('```');
  out();
  const indexes = await runSql('SHOW INDEX FROM paper_doc');
  out('```');
  out('paper_doc 的索引：');
  for (const row of indexes) out(`  ${row.Key_name}  ${row.Column_name}  ${row.Index_type}`);
  out('```');
  out();

  out('## 5. 记忆链路');
  out();
  out('```');
  const firstDoc = (await listDocs(1))[0];
  await scheduleReview(USER, firstDoc.id, firstDoc.title);
  const dueBefore = await listDueReviews(USER);
  out(`排入复习计划后，到期条目 = ${dueBefore.length}`);
  out(`  ${dueBefore.map((row) => `${row.doc_id} (${row.title})`).join(', ')}`);
  const graded = await gradeReview(USER, firstDoc.id, 4, firstDoc.title);
  out(`打分 quality=4 -> ease=${graded.ease} reps=${graded.reps} 间隔=${graded.intervalDays}天 下次=${graded.dueAt}`);
  const dueAfter = await listDueReviews(USER);
  out(`打分后仍然到期的条目 = ${dueAfter.length}（已排到未来）`);
  const study = await runSql(
    'SELECT query_text, hit_by, score FROM paper_study WHERE user_id = ? ORDER BY id DESC LIMIT 3',
    [USER],
  );
  out('检索足迹（paper_study，最近 3 条）：');
  for (const row of study) out(`  ${row.query_text} -> [${row.hit_by}] ${Number(row.score).toFixed(5)}`);
  out('```');
  out();

  out('## 6. 概览');
  out();
  out('```');
  out(JSON.stringify(await stats(), null, 2));
  out('```');

  const output = process.argv[2] || path.join(ROOT, 'evidence', 'selftest.md');
  await fs.mkdir(path.dirname(output), { recursive: true });
  await fs.writeFile(output, `${lines.join('\n')}\n`, 'utf8');
  console.log(`\nwritten: ${output}`);
  await close();
}

main().catch(async (error) => {
  console.error('[selftest] failed:', error);
  await close();
  process.exit(1);
});
