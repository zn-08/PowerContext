/**
 * embedders/openai.js —— 外部 embedding：OpenAI 兼容的 /v1/embeddings
 *
 * 配置（见 README「向量方案」）：
 *   EMBED_PROVIDER=openai
 *   EMBED_API_KEY  必填
 *   EMBED_BASE_URL 默认 https://api.openai.com/v1，也可填自建/网关地址
 *   EMBED_MODEL    默认 text-embedding-3-small
 *   EMBED_DIM      必须等于模型输出维度，否则启动即报错
 *
 * 与 local provider 的契约一致：embed(text, idf, dim) -> 长度 dim、L2 归一化的向量。
 * idf 在这里不使用——语义相似度由模型自己负责，seekdb 侧的 df 表仍然照常维护全文统计。
 */
import { config } from '../config.js';

export const name = 'openai';

/** 单次请求的最长等待时间，避免 base url 配错时卡住启动 */
const REQUEST_TIMEOUT_MS = 30_000;

function openaiConfig() {
  return config.embed.openai;
}

/** 配置缺失时给出可以直接照着改的提示 */
export function assertConfig() {
  const { apiKey, baseUrl, model } = openaiConfig();
  const missing = [];
  if (!apiKey) missing.push('EMBED_API_KEY');
  if (!baseUrl) missing.push('EMBED_BASE_URL');
  if (!model) missing.push('EMBED_MODEL');
  if (missing.length) {
    throw new Error(
      `[embed:openai] EMBED_PROVIDER=openai 需要配置 ${missing.join('、')}。`
        + `示例：EMBED_PROVIDER=openai EMBED_API_KEY=sk-xxx EMBED_MODEL=text-embedding-3-small EMBED_DIM=1536。`
        + '如果只是想先把服务跑起来，去掉 EMBED_PROVIDER 即可回到默认的 local。',
    );
  }
}

function embeddingsUrl() {
  const base = openaiConfig().baseUrl.replace(/\/+$/, '');
  return /\/embeddings$/.test(base) ? base : `${base}/embeddings`;
}

function dimensionMismatch(received, expected) {
  const { model } = openaiConfig();
  return new Error(
    `[embed:openai] 维度不匹配：模型 "${model}" 返回 ${received} 维，但 EMBED_DIM=${expected}。`
      + ` 二选一：① 设 EMBED_DIM=${received}，并先把旧向量列删掉再重建`
      + `（paper_doc.emb 现在是 VECTOR(${expected})，执行 DROP TABLE paper_doc; 之后跑 npm run setup-db）；`
      + `② 换成输出 ${expected} 维的模型（改 EMBED_MODEL）。`,
  );
}

async function requestEmbedding(text) {
  const { apiKey, model } = openaiConfig();
  const url = embeddingsUrl();
  let response;
  try {
    response = await fetch(url, {
      method: 'POST',
      headers: { 'content-type': 'application/json', authorization: `Bearer ${apiKey}` },
      body: JSON.stringify({ model, input: [text] }),
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
  } catch (error) {
    throw new Error(
      `[embed:openai] 请求 ${url} 失败：${error.message}。请检查 EMBED_BASE_URL 是否可达（当前 ${openaiConfig().baseUrl}）。`,
    );
  }
  if (!response.ok) {
    const detail = await response.text().catch(() => '');
    throw new Error(
      `[embed:openai] ${url} 返回 HTTP ${response.status} ${response.statusText}`
        + `：${detail.slice(0, 300)}（模型=${model}）`,
    );
  }
  let payload;
  try {
    payload = await response.json();
  } catch (error) {
    throw new Error(`[embed:openai] ${url} 的响应不是合法 JSON：${error.message}`);
  }
  const vector = payload?.data?.[0]?.embedding;
  if (!Array.isArray(vector)) {
    throw new Error(`[embed:openai] 响应里找不到 data[0].embedding，返回内容：${JSON.stringify(payload).slice(0, 300)}`);
  }
  return vector;
}

/** L2 归一化，和 local provider 用同一套约定 */
function normalizeL2(vector) {
  let norm = 0;
  for (const value of vector) norm += value * value;
  norm = Math.sqrt(norm);
  if (norm === 0) return vector.map(() => 0);
  return vector.map((value) => (Number.isFinite(value) ? value / norm : 0));
}

/**
 * @param {string} text
 * @param {Map<string, number>} idf  本地 provider 使用，这里忽略
 * @param {number} dim
 * @returns {Promise<number[]>}
 */
export async function embed(text, idf, dim) {
  assertConfig();
  const raw = await requestEmbedding(String(text ?? ''));
  if (raw.length !== dim) throw dimensionMismatch(raw.length, dim);
  return normalizeL2(raw);
}

/** 启动自检：真的打一次接口，把维度/鉴权问题挡在业务请求之前 */
export async function assertReady(dim) {
  assertConfig();
  await embed('papercompass embedder probe', new Map(), dim);
  return { provider: name, dim, model: openaiConfig().model };
}
