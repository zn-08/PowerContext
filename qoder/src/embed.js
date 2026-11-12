/**
 * embed.js —— 向量 provider 的选择入口
 *
 * 两种 provider，输出契约完全一致（长度 === EMBED_DIM，L2 归一化，可直接 cosine_distance）：
 *   local  （默认）：本地特征哈希 TF-IDF，零依赖、离线可跑，见 src/embedders/local.js
 *   openai          ：OpenAI 兼容的 /v1/embeddings，语义能力更强，见 src/embedders/openai.js
 *
 * 由 EMBED_PROVIDER 选择；调用方仍然只认 embed(text, idf, dim) 与 toVectorLiteral(vector)。
 * 唯一区别是 openai provider 要发一次网络请求，所以它的 embed() 返回 Promise——
 * 调用处统一 `await embed(...)`：本地路径仍然同步返回，结果与改造前逐位一致。
 */
import { config } from './config.js';
import * as localEmbedder from './embedders/local.js';
import * as openaiEmbedder from './embedders/openai.js';

// 分词与词频统计的口径归 local provider 所有，检索侧复用同一套实现
export { STOPWORDS, tokenize, termFreq, buildIdf } from './embedders/local.js';

const PROVIDERS = {
  local: localEmbedder,
  openai: openaiEmbedder,
};

let selected = null;

/** 维度必须是正整数，否则 EMBED_DIM 写错时只会得到一堆看不懂的报错 */
function assertDimension(dim) {
  if (!Number.isInteger(dim) || dim <= 0) {
    throw new Error(
      `[embed] 向量维度无效：${dim}。EMBED_DIM 必须是正整数（默认 384），`
        + '且要和 paper_doc.emb 的 VECTOR(维度) 一致。',
    );
  }
  return dim;
}

function resolveProvider() {
  if (selected) return selected;
  const name = config.embed.provider;
  const provider = Object.hasOwn(PROVIDERS, name) ? PROVIDERS[name] : null;
  if (!provider) {
    throw new Error(
      `[embed] 未知的 EMBED_PROVIDER=${name}。可选值：${Object.keys(PROVIDERS).join(' | ')}（留空则用 local）。`,
    );
  }
  if (name === 'openai') openaiEmbedder.assertConfig();
  selected = provider;
  return provider;
}

/**
 * 计算一条文本的稠密向量。
 * @param {string} text
 * @param {Map<string, number>} idf
 * @param {number} [dim]
 * @returns {number[] | Promise<number[]>}
 */
export function embed(text, idf, dim = config.dim) {
  return resolveProvider().embed(text, idf, assertDimension(dim));
}

/**
 * 启动自检：校验维度配置，并让 provider 自己确认服务可用。
 * local 是空操作；openai 会真的打一次接口，把鉴权与维度问题挡在业务请求之前。
 * @param {number} [dim]
 * @returns {Promise<{provider: string, dim: number}>}
 */
export async function assertEmbedderReady(dim = config.dim) {
  assertDimension(dim);
  return await resolveProvider().assertReady(dim);
}

/** 逗号分隔的小数向量 -> seekdb VECTOR 字面量 */
export function toVectorLiteral(vector) {
  return `[${vector.map((v) => (Number.isFinite(v) ? v : 0).toFixed(6)).join(',')}]`;
}
