/**
 * scripts/fetch-model.js — 下载本地模型（离线可用）
 *
 * 用法： node scripts/fetch-model.js
 * 目标： models/<model_id>/...  （与 Transformers.js 的 env.localModelPath 对应）
 *
 * 下载清单来自 HuggingFace 仓库 Xenova/all-MiniLM-L6-v2：
 *   config.json / tokenizer.json / tokenizer_config.json
 *   onnx/model.onnx            (FP32, 精度基线)
 *   onnx/model_fp16.onnx       (FP16)
 *   onnx/model_quantized.onnx  (INT8, 默认)
 */
import fs from 'node:fs/promises';
import path from 'node:path';
import { config } from '../server/config.js';
import { fetchBuffer } from './lib/downloader.js';

const MODEL_ID = process.env.MODEL_ID || config.modelId;
const REPO = process.env.MODEL_REPO || 'Xenova/all-MiniLM-L6-v2';
const HF_BASE = process.env.HF_BASE || 'https://huggingface.co';

const FILES = [
  'config.json',
  'tokenizer.json',
  'tokenizer_config.json',
  'special_tokens_map.json',
  'onnx/model.onnx',
  'onnx/model_fp16.onnx',
  'onnx/model_quantized.onnx',
];

const outRoot = path.join(config.modelsDir, REPO);

async function sizeOf(url) {
  try {
    const buf = await fetchBuffer(url);
    return buf.length;
  } catch {
    return null;
  }
}

async function download(url, dest) {
  await fs.mkdir(path.dirname(dest), { recursive: true });
  const buf = await fetchBuffer(url);
  await fs.writeFile(dest, buf);
  return buf.length;
}

async function main() {
  console.log(`model repo : ${REPO}`);
  console.log(`target dir : ${outRoot}`);
  const report = [];
  for (const f of FILES) {
    const url = `${HF_BASE}/${REPO}/resolve/main/${f}`;
    const dest = path.join(outRoot, f);
    try {
      await fs.access(dest);
      const st = await fs.stat(dest);
      console.log(`[skip] ${f} (${(st.size / 1048576).toFixed(2)} MB, already present)`);
      report.push({ file: f, bytes: st.size, status: 'cached' });
      continue;
    } catch { /* not present */ }
    const t0 = Date.now();
    try {
      const n = await download(url, dest);
      console.log(`[ok]   ${f}  ${(n / 1048576).toFixed(2)} MB  ${Date.now() - t0} ms`);
      report.push({ file: f, bytes: n, status: 'downloaded' });
    } catch (e) {
      console.log(`[miss] ${f}: ${e.message}`);
      report.push({ file: f, bytes: 0, status: 'missing' });
    }
  }
  console.log('\n--- 体积汇总 ---');
  const groups = { fp32: 0, fp16: 0, int8: 0, other: 0 };
  for (const r of report) {
    if (r.file.endsWith('model.onnx')) groups.fp32 += r.bytes;
    else if (r.file.endsWith('model_fp16.onnx')) groups.fp16 += r.bytes;
    else if (r.file.endsWith('model_quantized.onnx')) groups.int8 += r.bytes;
    else groups.other += r.bytes;
  }
  const mb = (b) => (b / 1048576).toFixed(2);
  console.log(`FP32 onnx : ${mb(groups.fp32)} MB`);
  console.log(`FP16 onnx : ${mb(groups.fp16)} MB  (${(groups.fp32 ? (100 * groups.fp16 / groups.fp32).toFixed(1) : '-')}% of FP32)`);
  console.log(`INT8 onnx : ${mb(groups.int8)} MB  (${(groups.fp32 ? (100 * groups.int8 / groups.fp32).toFixed(1) : '-')}% of FP32)`);
  console.log(`其它(词表/配置) : ${mb(groups.other)} MB`);
  console.log(`压缩比 FP32/INT8 = ${groups.int8 ? (groups.fp32 / groups.int8).toFixed(2) : '-'}x`);
}

main().catch((e) => { console.error(e); process.exit(1); });
