/**
 * server/config.js — 全局配置（可用环境变量覆盖，便于离线/评审环境调整）
 */
import path from 'node:path';
import fs from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
export const ROOT = path.resolve(__dirname, '..');

const int = (v, d) => (v === undefined || v === '' ? d : Number.parseInt(v, 10));

export const config = {
  // ---- HTTP 服务 ----
  httpPort: int(process.env.PORT, 5173),
  httpHost: process.env.HOST || '127.0.0.1',

  // ---- seekdb 连接（Server 模式）----
  dbHost: process.env.SEEKDB_HOST || '127.0.0.1',
  dbPort: int(process.env.SEEKDB_PORT, 2881),
  dbUser: process.env.SEEKDB_USER || 'root',
  dbPassword: process.env.SEEKDB_PASSWORD ?? '',
  database: process.env.SEEKDB_DATABASE || 'stepfun_kb',
  table: process.env.SEEKDB_TABLE || 'stepfun_docs',

  // ---- 向量维度：Xenova/all-MiniLM-L6-v2 = 384 ----
  dim: int(process.env.EMBED_DIM, 384),

  // ---- 端侧模型（浏览器内）----
  modelId: process.env.MODEL_ID || 'Xenova/all-MiniLM-L6-v2',
  // 量化档位：q8 = INT8, fp16 = FP16, fp32 = 不量化(基线)
  dtype: process.env.MODEL_DTYPE || 'q8',
  // 浏览器从本地 /models 目录加载（离线可用）
  modelLocalPath: process.env.MODEL_LOCAL_PATH || '/models/',
  // 浏览器内是否允许联网下载模型（离线评审时设为 0）
  allowRemoteModels: process.env.ALLOW_REMOTE_MODELS !== '0',

  // ---- 本地目录 ----
  modelsDir: path.join(ROOT, 'models'),
  publicDir: path.join(ROOT, 'public'),
  dataDir: path.join(ROOT, 'data'),
  vendorDir: path.join(ROOT, 'node_modules', '@huggingface', 'transformers', 'dist'),
  ortDir: path.join(ROOT, 'node_modules', 'onnxruntime-web', 'dist'),
};

/** 扫描本地 models/ 目录，列出可离线使用的模型及其各量化档位的 onnx 体积 */
export async function listLocalModels() {
  const out = [];
  let orgs = [];
  try {
    orgs = await fs.readdir(config.modelsDir, { withFileTypes: true });
  } catch {
    return out;
  }
  for (const org of orgs.filter((d) => d.isDirectory())) {
    let repos = [];
    try {
      repos = await fs.readdir(path.join(config.modelsDir, org.name), { withFileTypes: true });
    } catch { continue; }
    for (const repo of repos.filter((d) => d.isDirectory())) {
      const id = `${org.name}/${repo.name}`;
      const sizes = {};
      for (const [dtype, file] of Object.entries({
        q8: 'onnx/model_quantized.onnx',
        fp16: 'onnx/model_fp16.onnx',
        fp32: 'onnx/model.onnx',
      })) {
        try {
          const st = await fs.stat(path.join(config.modelsDir, org.name, repo.name, file));
          sizes[dtype] = st.size;
        } catch { /* 该档位未下载 */ }
      }
      if (!Object.keys(sizes).length) continue;
      out.push({ id, dim: config.dim, sizes });
    }
  }
  return out.sort((a, b) => a.id.localeCompare(b.id));
}
