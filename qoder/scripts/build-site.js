/**
 * build-site.js —— 生成可发布到 Qoder Sites 的静态站点
 *
 *   node scripts/build-site.js
 *
 * 产出 site/ 目录：
 *   index.html       项目展示页（静态，直接可托管）
 *   snapshot.json    真实 seekdb 运行结果快照（演示用）
 *   screenshots/     界面截图
 *
 * 说明：站点是**作品展示与评审入口**，不单独评分。页面里的检索结果来自
 * 真实运行 seekdb 后导出的快照；评审想跑实时检索，按 README 起本地服务即可。
 */
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { close, ensureSchema, search, stats } from '../src/db.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '..');
const SITE = path.join(ROOT, 'site');

const DEMO_QUERIES = [
  { q: '混合检索怎么融合两路结果', note: '中文语义问句' },
  { q: 'HNSW 的 ef_search 调到多少合适', note: '中文 + 英文术语混排' },
  { q: 'reciprocal rank fusion', note: '英文术语' },
  { q: '复习笔记应该怎么写', note: '学习场景' },
];

async function main() {
  await ensureSchema();
  const snapshot = { generatedAt: new Date().toISOString(), stats: await stats(), queries: [] };
  for (const item of DEMO_QUERIES) {
    const result = await search({ query: item.q, topK: 5 });
    snapshot.queries.push({
      query: item.q,
      note: item.note,
      mode: result.mode,
      sql: result.sql,
      hits: result.hits,
    });
  }

  await fs.mkdir(path.join(SITE, 'screenshots'), { recursive: true });
  await fs.writeFile(
    path.join(SITE, 'snapshot.json'),
    `${JSON.stringify(snapshot, null, 2)}\n`,
    'utf8',
  );

  // 复制界面截图（如果已经用无头浏览器截好）
  const shots = path.join(ROOT, 'evidence', 'screenshots');
  try {
    for (const name of await fs.readdir(shots)) {
      if (!/\.(png|jpe?g)$/i.test(name)) continue;
      await fs.copyFile(path.join(shots, name), path.join(SITE, 'screenshots', name));
    }
  } catch {
    /* 没有截图也不影响站点 */
  }

  console.log(`[site] snapshot written: ${snapshot.queries.length} queries, ${snapshot.stats.docs} docs`);
  await close();
}

main().catch(async (error) => {
  console.error('[site] failed:', error);
  await close();
  process.exit(1);
});
