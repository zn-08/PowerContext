/**
 * scripts/browser-test.js — 真实浏览器端到端验证（Chrome / Edge）
 *
 * 驱动系统已安装的 Chrome/Edge（无需下载 Playwright 自带浏览器），
 * 在真实浏览器环境中验证：
 *   ① 页面能启动，模型在浏览器内加载（WebGPU 或降级到 WASM）
 *   ② 载入演示语料 -> 浏览器内向量化 -> 写入 seekdb
 *   ③ 输入查询 -> 浏览器内查询向量化 -> seekdb 混合检索 -> 展示排序结果
 *   ④ 界面明确显示当前推理后端
 *   ⑤ 增量导入后新文档可被检索到
 *
 * 用法： node scripts/browser-test.js [--headed] [--url http://127.0.0.1:5173]
 */
import fs from 'node:fs/promises';
import path from 'node:path';
import { chromium } from 'playwright-core';
import { config, ROOT } from '../server/config.js';

const args = process.argv.slice(2);
const headed = args.includes('--headed');
const urlArg = args.find((a) => a.startsWith('--url='));
const BASE = urlArg ? urlArg.split('=')[1] : `http://${config.httpHost}:${config.httpPort}`;

const CHROME_CANDIDATES = [
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
];

async function findBrowser() {
  for (const p of CHROME_CANDIDATES) {
    try { await fs.access(p); return p; } catch { /* next */ }
  }
  return null;
}

const out = [];
const log = (s) => { console.log(s); out.push(s); };
let failed = 0;
const check = (ok, label, extra = '') => {
  log(`  ${ok ? '\x1b[32mPASS\x1b[0m' : '\x1b[31mFAIL\x1b[0m'} ${label}${extra ? '  ' + extra : ''}`);
  if (!ok) failed += 1;
  return ok;
};

async function main() {
  const exe = await findBrowser();
  if (!exe) {
    log('未找到 Chrome/Edge，无法执行浏览器内验证');
    process.exitCode = 2;
    return;
  }
  log(`浏览器: ${exe}`);
  log(`应用地址: ${BASE}\n`);

  const browser = await chromium.launch({
    executablePath: exe,
    headless: !headed,
    args: [
      '--enable-unsafe-webgpu',
      '--enable-features=Vulkan,UseSkiaRenderer',
      '--use-angle=swiftshader',
      '--no-sandbox',
      '--disable-dev-shm-usage',
    ],
  });
  const ctx = await browser.newContext({ viewport: { width: 1600, height: 1200 } });
  const page = await ctx.newPage();
  const consoleErrors = [];
  // 自动接受 confirm（例如「清空集合」的确认框）
  page.on('dialog', (d) => d.accept().catch(() => {}));
  page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text()); });
  page.on('pageerror', (e) => consoleErrors.push('pageerror: ' + e.message));

  log('[1] 打开应用页面');
  await page.goto(BASE, { waitUntil: 'domcontentloaded', timeout: 60000 });

  // 等待模型加载完成（device 字段不再是 —）
  log('[2] 等待浏览器内模型加载 …');
  const tLoad = Date.now();
  let loaded = false;
  while (Date.now() - tLoad < 300000) {
    const st = await page.evaluate(() => ({
      device: document.getElementById('mDevice')?.textContent?.trim(),
      progress: document.getElementById('modelProgressText')?.textContent?.trim(),
    }));
    if (st.device && st.device !== '—') { loaded = true; break; }
    if (st.progress && st.progress !== '—' && st.progress !== st.lastLogged) {
      st.lastLogged = st.progress;
      log(`      … ${st.progress}`);
    }
    await page.waitForTimeout(1000);
  }
  if (!loaded) {
    const dump = await page.evaluate(() => ({
      progress: document.getElementById('modelProgressText')?.textContent,
      device: document.getElementById('mDevice')?.textContent,
      fallback: document.getElementById('mFallback')?.textContent,
    }));
    log('      模型加载未完成，最后状态: ' + JSON.stringify(dump));
    if (consoleErrors.length) consoleErrors.slice(0, 10).forEach((e) => log('   ERR ' + e.slice(0, 300)));
  }
  const model = await page.evaluate(() => ({
    device: document.getElementById('mDevice').textContent.trim(),
    dtype: document.getElementById('dtypeValue').textContent.trim(),
    fallback: document.getElementById('mFallback').textContent.trim(),
    size: document.getElementById('mSize').textContent.trim(),
    loadMs: document.getElementById('mLoadMs').textContent.trim(),
    dim: document.getElementById('mDim').textContent.trim(),
    badge: document.getElementById('deviceValue').textContent.trim(),
  }));
  log(`      推理后端=${model.device} 量化=${model.dtype} 体积=${model.size} 加载=${model.loadMs}`);
  log(`      降级说明=${model.fallback}`);
  check(['WebGPU', 'WASM'].includes(model.device), '模型在浏览器内加载完成', `backend=${model.device}`);
  check(model.device === model.badge, '界面徽章与实际后端一致');
  check(model.dim === String(config.dim), '向量维度与建表一致', `dim=${model.dim}`);

  // 记录浏览器端能力
  const caps = await page.evaluate(async () => ({
    hasGpu: !!navigator.gpu,
    ua: navigator.userAgent,
  }));
  log(`      navigator.gpu=${caps.hasGpu}`);
  log(`      UA=${caps.ua}`);

  log('\n[3] 清空集合并载入演示语料');
  await page.click('#btnReset');
  await page.waitForFunction(
    () => document.getElementById('sCount').textContent.trim() === '0',
    null,
    { timeout: 60000 }
  );
  const cleared = await page.evaluate(() => document.getElementById('sCount').textContent.trim());
  check(cleared === '0', '集合已清空', `docCount=${cleared}`);
  await page.click('#btnLoadCorpus');
  await page.waitForFunction(() => document.getElementById('docInput').value.length > 100, null, { timeout: 20000 });
  const corpusLen = await page.evaluate(() => document.getElementById('docInput').value.split('\n').filter(Boolean).length);
  check(corpusLen > 10, '演示语料已载入', `${corpusLen} 篇`);

  log('\n[4] 浏览器内向量化并写入 seekdb');
  const totalBefore = await page.evaluate(() => document.getElementById('iTotal').textContent.trim());
  await page.click('#btnImport');
  await page.waitForFunction(
    (prev) => {
      const cur = document.getElementById('iTotal').textContent.trim();
      return !window.__appBusy && cur !== prev && cur !== '—';
    },
    totalBefore,
    { timeout: 180000 }
  );
  const imp = await page.evaluate(() => ({
    count: document.getElementById('iCount').textContent.trim(),
    embedMs: document.getElementById('iEmbedMs').textContent.trim(),
    throughput: document.getElementById('iThroughput').textContent.trim(),
    writeMs: document.getElementById('iWriteMs').textContent.trim(),
    refreshMs: document.getElementById('iRefreshMs').textContent.trim(),
    total: document.getElementById('iTotal').textContent.trim(),
  }));
  log(`      ${imp.count} 篇 | 向量化 ${imp.embedMs} (${imp.throughput}) | 写入 ${imp.writeMs} | 刷新 ${imp.refreshMs} | 集合总数 ${imp.total}`);
  check(Number(imp.total) >= corpusLen, '文档已写入 seekdb', `total=${imp.total}`);

  log('\n[5] 查询并展示排序结果');
  await page.fill('#queryInput', '端侧模型量化能省多少体积');
  const e2eBefore = await page.evaluate(() => document.getElementById('qE2eMs').textContent.trim());
  await page.click('#btnSearch');
  await page.waitForFunction(
    (prev) => {
      const cur = document.getElementById('qE2eMs').textContent.trim();
      return !window.__appBusy && cur !== prev && cur !== '—';
    },
    e2eBefore,
    { timeout: 120000 }
  );
  await page.waitForTimeout(500);
  const q = await page.evaluate(() => ({
    embedMs: document.getElementById('qEmbedMs').textContent.trim(),
    dbMs: document.getElementById('qDbMs').textContent.trim(),
    e2eMs: document.getElementById('qE2eMs').textContent.trim(),
    hit: document.getElementById('qHit').textContent.trim(),
    results: Array.from(document.querySelectorAll('#results .result')).map((el) => ({
      title: el.querySelector('.title')?.textContent?.trim(),
      meta: el.querySelector('.meta')?.textContent?.trim(),
      tag: el.querySelector('.tag')?.textContent?.trim(),
    })),
    sql: document.getElementById('sqlBox').textContent.slice(0, 4000),
  }));
  log(`      查询向量化 ${q.embedMs} | seekdb 检索 ${q.dbMs} | 端到端 ${q.e2eMs} | ${q.hit}`);
  q.results.forEach((r, i) => log(`      #${i + 1} [${r.tag}] ${r.title}  ${r.meta}`));
  check(q.results.length > 0, '检索结果已展示', `${q.results.length} 条`);
  check(/MATCH|match/.test(q.sql) && /cosine_distance|_distance|knn/i.test(q.sql),
    '展示的混合检索 SQL 同时包含全文与向量候选');

  log('\n[6] 增量导入后新文档可被检索到');
  const incText = '增量导入验证文档：边缘设备上的向量缓存与增量索引刷新机制';
  await page.evaluate((t) => {
    document.getElementById('docInput').value = t;
  }, incText);
  const totalBeforeInc = await page.evaluate(() => document.getElementById('iTotal').textContent.trim());
  await page.click('#btnImport');
  await page.waitForFunction(
    (prev) => {
      const cur = document.getElementById('iTotal').textContent.trim();
      return !window.__appBusy && cur !== prev && cur !== '—';
    },
    totalBeforeInc,
    { timeout: 120000 }
  );
  const total2 = await page.evaluate(() => document.getElementById('iTotal').textContent.trim());
  await page.fill('#queryInput', '边缘设备上的向量缓存');
  await page.click('#btnSearch');
  await page.waitForTimeout(2500);
  const incHit = await page.evaluate(() =>
    Array.from(document.querySelectorAll('#results .result')).map((el) => el.textContent).join(' ')
  );
  check(Number(total2) > Number(imp.total), '增量导入后集合计数增加', `${imp.total} -> ${total2}`);
  check(incHit.includes('增量导入验证文档'), '增量导入的新文档可被检索到');

  log('\n[7] 截图');
  const shot = path.join(ROOT, 'data', 'browser-test.png');
  await page.screenshot({ path: shot, fullPage: true });
  log(`      已保存 ${shot}`);

  if (consoleErrors.length) {
    log('\n浏览器控制台错误:');
    consoleErrors.slice(0, 10).forEach((e) => log('   ' + e.slice(0, 200)));
  } else {
    log('\n浏览器控制台无错误');
  }

  log(`\n===== 浏览器端验证结果: ${failed === 0 ? '全部通过' : failed + ' 项失败'} =====`);
  await fs.writeFile(path.join(ROOT, 'data', 'browser-test-report.txt'), out.join('\n'), 'utf8');
  await browser.close();
  process.exitCode = failed === 0 ? 0 : 1;
}

main().catch((e) => {
  console.error('浏览器测试异常:', e);
  process.exitCode = 1;
});
