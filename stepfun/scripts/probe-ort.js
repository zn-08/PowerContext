/**
 * scripts/probe-ort.js — 浏览器内诊断：为什么 wasm 降级失败？
 * 用法： node scripts/probe-ort.js
 */
import { chromium } from 'playwright-core';

const BASE = process.env.BASE_URL || 'http://127.0.0.1:5173';
const EXE = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';

const browser = await chromium.launch({
  executablePath: EXE,
  headless: true,
  args: ['--enable-unsafe-webgpu', '--use-angle=swiftshader', '--no-sandbox'],
});
const page = await browser.newPage();
page.on('console', (m) => console.log('   [console]', m.type(), m.text().slice(0, 300)));

// 拦截 app.js，避免应用的自动加载干扰探测
await page.route('**/app.js', (route) => route.abort());
await page.goto(BASE, { waitUntil: 'domcontentloaded' });

console.log('=== 探测 1：先探测 requestAdapter，再按 wasm 顺序加载 ===');
const r1 = await page.evaluate(async () => {
  const out = {};
  out.hasNavigatorGpu = !!navigator.gpu;
  try {
    const ad = await navigator.gpu?.requestAdapter();
    out.adapter = ad ? 'ok' : 'null';
  } catch (e) { out.adapter = 'throw: ' + e.message; }
  const mod = await import('/vendor/transformers/transformers.min.js');
  const { env, pipeline } = mod;
  env.allowLocalModels = true;
  env.localModelPath = '/models/';
  env.allowRemoteModels = false;
  env.backends.onnx.wasm.wasmPaths = '/vendor/ort/';
  for (const d of ['wasm', 'webgpu']) {
    try {
      const p = await pipeline('feature-extraction', 'Xenova/all-MiniLM-L6-v2', { device: d, dtype: 'q8' });
      const r = await p(['hello world'], { pooling: 'mean', normalize: true });
      out[d] = `OK dims=${JSON.stringify(r.dims)}`;
    } catch (e) { out[d] = 'ERR ' + String(e.message).slice(0, 260); }
  }
  return out;
});
console.log(JSON.stringify(r1, null, 2));

console.log('\n=== 探测 2：新页面，先 webgpu 再 wasm（复现 app 现状） ===');
const page2 = await browser.newPage();
await page2.route('**/app.js', (route) => route.abort());
await page2.goto(BASE, { waitUntil: 'domcontentloaded' });
const r2 = await page2.evaluate(async () => {
  const out = {};
  const mod = await import('/vendor/transformers/transformers.min.js');
  const { env, pipeline } = mod;
  env.allowLocalModels = true;
  env.localModelPath = '/models/';
  env.allowRemoteModels = false;
  env.backends.onnx.wasm.wasmPaths = '/vendor/ort/';
  for (const d of ['webgpu', 'wasm']) {
    try {
      const p = await pipeline('feature-extraction', 'Xenova/all-MiniLM-L6-v2', { device: d, dtype: 'q8' });
      const r = await p(['hello world'], { pooling: 'mean', normalize: true });
      out[d] = `OK dims=${JSON.stringify(r.dims)}`;
    } catch (e) { out[d] = 'ERR ' + String(e.message).slice(0, 260); }
  }
  return out;
});
console.log(JSON.stringify(r2, null, 2));

console.log('\n=== 探测 3：不设置 wasmPaths（用 transformers 自带 dist 路径） ===');
const page3 = await browser.newPage();
await page3.route('**/app.js', (route) => route.abort());
await page3.goto(BASE, { waitUntil: 'domcontentloaded' });
const r3 = await page3.evaluate(async () => {
  const out = {};
  const mod = await import('/vendor/transformers/transformers.min.js');
  const { env, pipeline } = mod;
  env.allowLocalModels = true;
  env.localModelPath = '/models/';
  env.allowRemoteModels = false;
  // 不覆盖 wasmPaths，指向 transformers 自带的 dist（同目录有 .jsep.wasm）
  env.backends.onnx.wasm.wasmPaths = '/vendor/transformers/';
  try {
    const p = await pipeline('feature-extraction', 'Xenova/all-MiniLM-L6-v2', { device: 'wasm', dtype: 'q8' });
    const r = await p(['hello world'], { pooling: 'mean', normalize: true });
    out.wasm = `OK dims=${JSON.stringify(r.dims)}`;
  } catch (e) { out.wasm = 'ERR ' + String(e.message).slice(0, 300); }
  return out;
});
console.log(JSON.stringify(r3, null, 2));

await browser.close();
