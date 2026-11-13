/**
 * embedders/local.js —— 本地特征哈希（feature hashing）TF-IDF 向量
 *
 * 为什么保留一个完全本地的 provider？
 *   本作品要求「评审按运行说明配置后就能跑起来」。任何在线 embedding 服务都需要
 *   API Key 或模型下载，会把评审环境卡在第一步。所以默认走**完全本地、零依赖**的
 *   特征哈希向量：
 *     1. 分词：拉丁词按字母数字切分；中日韩字符切成「单字 + 相邻二元组」，
 *        让中文短语也能形成稳定特征；
 *     2. 哈希：FNV-1a 把每个词映射到 dim 维中的一维，并带一个正负号（sign hashing），
 *        降低哈希冲突造成的系统性偏差；
 *     3. 加权：TF 用 sublinear（1 + ln tf），IDF 由 seekdb 里的 `paper_term` 表提供
 *        （df 是数据库统计出来的，不是本地猜的）；
 *     4. 归一化：L2 归一化，之后可以直接用 cosine_distance。
 *
 * 契约（所有 provider 一致，见 src/embedders/openai.js）：
 *   embed(text, idf, dim) -> number[]（长度 === dim，L2 归一化）
 *   assertReady(dim)      -> 启动时的自检
 */

import { STOPWORDS, tokenize } from '../tokenize.js';

// 分词规则只有一份实现（src/tokenize.js），前端加载的是同一个文件
export { STOPWORDS, tokenize };

/** FNV-1a 32 位哈希 */
function fnv1a(text) {
  let hash = 0x811c9dc5;
  for (let i = 0; i < text.length; i += 1) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash >>> 0;
}

/**
 * 词频统计。
 * @param {string} text
 * @returns {Map<string, number>}
 */
export function termFreq(text) {
  const tf = new Map();
  for (const token of tokenize(text)) {
    tf.set(token, (tf.get(token) || 0) + 1);
  }
  return tf;
}

/**
 * 由 df 表构造 idf 映射。df 来自 seekdb 的 paper_term 表。
 * @param {Map<string, number>} dfMap
 * @param {number} docCount
 * @returns {Map<string, number>}
 */
export function buildIdf(dfMap, docCount) {
  const idf = new Map();
  const total = Math.max(docCount, 1);
  for (const [term, df] of dfMap.entries()) {
    idf.set(term, Math.log((total + 1) / (df + 1)) + 1);
  }
  return idf;
}

/**
 * 计算一条文本的稠密向量。
 * @param {string} text
 * @param {Map<string, number>} idf
 * @param {number} dim
 * @returns {number[]}
 */
export function embed(text, idf, dim) {
  const tf = termFreq(text);
  const vector = new Array(dim).fill(0);
  for (const [term, count] of tf.entries()) {
    const hash = fnv1a(term);
    const index = hash % dim;
    const sign = (fnv1a(`${term}#sign`) & 1) === 0 ? 1 : -1;
    const weight = (1 + Math.log(count)) * (idf.get(term) ?? 1);
    vector[index] += sign * weight;
  }
  let norm = 0;
  for (const value of vector) norm += value * value;
  norm = Math.sqrt(norm);
  if (norm > 0) {
    for (let i = 0; i < dim; i += 1) vector[i] = Number((vector[i] / norm).toFixed(6));
  }
  return vector;
}

export const name = 'local';

/** 本地 provider 不依赖外部服务，启动自检是空操作（维度校验在 src/embed.js 统一做） */
export async function assertReady(dim) {
  return { provider: name, dim };
}
