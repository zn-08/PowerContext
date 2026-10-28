/**
 * scripts/browser-bench.js — 浏览器内分阶段基准（首次 / 预热后 / 中位数）
 *
 * 在真实浏览器里测量：
 *   ① 模型加载耗时        ② 文档向量化吞吐        ③ 写入与索引刷新
 *   ④ 查询向量化          ⑤ seekdb 检索耗时        ⑥ 提交查询 -> 结果可展示（端到端）
 *
 * 用法： node scripts/browser-bench.js [--rounds 5]
 */
import fs from 'node:fs/promises';
import path from 'node:path';
import { chromium } from 'playwright-core';
import { config, ROOT } from '../server/config.js';

const roundsArg = process.argv.find((a) => a.startsWith('--rounds='));
const ROUNDS = roundsArg ? Number(roundsArg.split('=')[1]) : 5;
const BASE = process.env.BASE_URL || `http://${config.httpHost}:${config.httpPort}`;
const EXE = [
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
].find((p) => { try { return require('node:fs').existsSync(p); } catch { return false; } })
  || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';

const median = (a) => {
  const s = [...a].sort((x, y) => x - y);
  return s.length % 2 ? s[(s.length - 1) / 2] : (s[s.length / 2 - 1] + s[s.length / 2]) / 2;
};
const f2 = (v) => Number(v).toFixed(2);

const browser = await chromium.launch({
  executablePath: EXE,
  headless: true,
  args: ['--enable-unsafe-webgpu', '--use-angle=swiftshader', '--no-sandbox'],
});
const page = await browser.newPage({ viewport: { width: 1600, height: 1200 } });
page.on('dialog', (d) => {
  console.log(`   [dialog] ${d.type()}: ${d.message().slice(0, 40)} -> accept`);
  d.accept().catch(() => {});
});
const errs = [];
page.on('pageerror', (e) => errs.push(e.message));

const lines = [];
const log = (s) => { console.log(s); lines.push(s); };

log('==========================================================');
log(' 浏览器内分阶段基准 (Chrome headless)');
log(` 目标: ${BASE}   轮次: ${ROUNDS}`);
log('==========================================================');

await page.goto(BASE, { waitUntil: 'domcontentloaded' });

// 等待模型加载完成
const t0 = Date.now();
while (Date.now() - t0 < 300000) {
  const d = await page.evaluate(() => document.getElementById('mDevice')?.textContent?.trim());
  if (d && d !== '—') break;
  await page.waitForTimeout(500);
}
const model = await page.evaluate(() => ({
  device: document.getElementById('mDevice').textContent.trim(),
  dtype: document.getElementById('dtypeValue').textContent.trim(),
  size: document.getElementById('mSize').textContent.trim(),
  loadMs: document.getElementById('mLoadMs').textContent.trim(),
  model: document.getElementById('modelValue').textContent.trim(),
  fallback: document.getElementById('mFallback').textContent.trim(),
}));
log(`  模型: ${model.model}  量化: ${model.dtype}  体积: ${model.size}`);
log(`  推理后端: ${model.device}   降级说明: ${model.fallback}`);
log(`  ① 模型加载(冷): ${model.loadMs}`);

// ② 文档向量化：在页面内直接调用 extractor，重复 rounds 轮
const embedBench = await page.evaluate(async (rounds) => {
  const corpus = await (await fetch('/data/corpus.json')).json();
  const texts = corpus.docs.map((d) => `${d.title}\n${d.text}`);
  // 页面里的 extractor 已加载；这里通过一次导入来触达内部状态不可行，
  // 改为直接使用 UI 的导入流程并读取其计时。
  return { docCount: texts.length, queries: corpus.queries.length };
}, ROUNDS);

/** 等待集合清空（带诊断） */
async function waitCleared(page) {
  for (let i = 0; i < 120; i++) {
    const st = await page.evaluate(() => ({
      count: document.getElementById('sCount')?.textContent?.trim(),
      busy: !!window.__appBusy,
    }));
    if (st.count === '0' && !st.busy) return st;
    if (i === 3) console.log(`   [诊断] sCount=${st.count} busy=${st.busy}`);
    await page.waitForTimeout(500);
  }
  const st = await page.evaluate(() => ({
    count: document.getElementById('sCount')?.textContent?.trim(),
    total: document.getElementById('iTotal')?.textContent?.trim(),
    results: document.getElementById('results')?.textContent?.trim()?.slice(0, 80),
  }));
  throw new Error('等待集合清空超时: ' + JSON.stringify(st));
}

// 用 UI 的导入流程做端到端写入测量（重复 rounds 轮，取中位数）
const importRuns = [];
for (let i = 0; i < ROUNDS; i++) {
  await page.click('#btnReset');
  await waitCleared(page);
  await page.click('#btnLoadCorpus');
  await page.waitForFunction(() => document.getElementById('docInput').value.length > 100, null, { timeout: 20000 });
  const before = await page.evaluate(() => document.getElementById('iTotal').textContent.trim());
  await page.click('#btnImport');
  await page.waitForFunction((prev) => {
    const cur = document.getElementById('iTotal').textContent.trim();
    return !window.__appBusy && cur !== prev && cur !== '—';
  }, before, { timeout: 180000 });
  const m = await page.evaluate(() => ({
    embedMs: parseFloat(document.getElementById('iEmbedMs').textContent),
    writeMs: parseFloat(document.getElementById('iWriteMs').textContent),
    refreshMs: parseFloat(document.getElementById('iRefreshMs').textContent),
    total: document.getElementById('iTotal').textContent.trim(),
  }));
  importRuns.push(m);
}
const embMed = median(importRuns.map((r) => r.embedMs));
const wMed = median(importRuns.map((r) => r.writeMs));
const rMed = median(importRuns.map((r) => r.refreshMs));
log('');
log(`  ② 文档向量化 ${embedBench.docCount} 篇 (UI 导入流程, n=${ROUNDS}): 首次 ${f2(importRuns[0].embedMs)} ms | 中位数 ${f2(embMed)} ms | 吞吐 ${f2(embedBench.docCount / (embMed / 1000))} 篇/秒`);
log(`  ③ 写入 seekdb: 首次 ${f2(importRuns[0].writeMs)} ms | 中位数 ${f2(wMed)} ms`);
log(`     索引刷新    : 首次 ${f2(importRuns[0].refreshMs)} ms | 中位数 ${f2(rMed)} ms`);

// ④⑤⑥ 查询
const queries = ['端侧模型量化能省多少体积', 'how to create a vector index in oceanbase', '浏览器里怎么让模型跑在 GPU 上', '全文和向量一起排序怎么实现', '增量导入后新文档什么时候能被搜到'];
const qRuns = [];
for (const q of queries) {
  await page.fill('#queryInput', q);
  const beforeE2e = await page.evaluate(() => document.getElementById('qE2eMs').textContent.trim());
  const t = Date.now();
  await page.click('#btnSearch');
  await page.waitForFunction((prev) => {
    const cur = document.getElementById('qE2eMs').textContent.trim();
    return !window.__appBusy && cur !== prev && cur !== '—';
  }, beforeE2e, { timeout: 60000 });
  const wall = Date.now() - t;
  const m = await page.evaluate(() => ({
    embedMs: parseFloat(document.getElementById('qEmbedMs').textContent),
    dbMs: parseFloat(document.getElementById('qDbMs').textContent),
    e2eMs: parseFloat(document.getElementById('qE2eMs').textContent),
  }));
  qRuns.push({ ...m, wallMs: wall });
}
const qEmb = median(qRuns.map((r) => r.embedMs));
const qDb = median(qRuns.map((r) => r.dbMs));
const qE2e = median(qRuns.map((r) => r.e2eMs));
const qWall = median(qRuns.map((r) => r.wallMs));
log('');
log(`  ④ 查询向量化      : 首次 ${f2(qRuns[0].embedMs)} ms | 中位数 ${f2(qEmb)} ms (n=${qRuns.length})`);
log(`  ⑤ seekdb 检索     : 首次 ${f2(qRuns[0].dbMs)} ms | 中位数 ${f2(qDb)} ms (n=${qRuns.length})`);
log(`  ⑥ 端到端(UI计时)  : 首次 ${f2(qRuns[0].e2eMs)} ms | 中位数 ${f2(qE2e)} ms (n=${qRuns.length})`);
log(`     端到端(脚本墙钟): 中位数 ${f2(qWall)} ms (含 playwright 交互开销)`);

log('');
log(errs.length ? `  页面错误: ${errs.slice(0, 3).join(' | ')}` : '  页面无错误');

await fs.writeFile(path.join(ROOT, 'data', 'browser-bench-report.txt'), lines.join('\n') + '\n', 'utf8');
console.log('\n已写入 data/browser-bench-report.txt');
await browser.close();
