/**
 * embed.js —— 本地向量：特征哈希（feature hashing）TF-IDF 向量
 *
 * 为什么不用外部 embedding 模型？
 *   本作品要求「评审按运行说明配置后就能跑起来」。任何在线 embedding 服务都需要
 *   API Key 或模型下载，会把评审环境卡在第一步。所以这里用**完全本地、零依赖**的
 *   特征哈希向量：
 *     1. 分词：拉丁词按字母数字切分；中日韩字符切成「单字 + 相邻二元组」，
 *        让中文短语也能形成稳定特征；
 *     2. 哈希：FNV-1a 把每个词映射到 dim 维中的一维，并带一个正负号（sign hashing），
 *        降低哈希冲突造成的系统性偏差；
 *    3. 加权：TF 用 sublinear（1 + ln tf），IDF 由 seekdb 里的 `paper_term` 表提供
 *        （df 是数据库统计出来的，不是本地猜的）；
 *    4. 归一化：L2 归一化，之后可以直接用 cosine_distance。
 *
 * 需要更强的语义能力时，`EMBED_PROVIDER=openai` + `OPENAI_API_KEY` 会切换到
 * 外部 embedding（向量维度必须与 EMBED_DIM 一致），其余流程完全不变。
 */

/** 中日韩统一表意文字区间（含扩展 A） */
const CJK = /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/;

/** 英文停用词 + 中文高频虚词，先滤掉以免主导余弦相似度 */
export const STOPWORDS = new Set([
  'the', 'a', 'an', 'and', 'or', 'of', 'to', 'in', 'is', 'are', 'was', 'were', 'be', 'been',
  'for', 'on', 'with', 'as', 'by', 'at', 'from', 'that', 'this', 'it', 'its', 'we', 'you',
  '的', '了', '和', '是', '在', '也', '就', '都', '而', '及', '与', '着', '或', '一个',
  '我们', '你们', '他们', '这个', '那个', '可以', '因为', '所以', '但是',
]);

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
 * 把文本切成检索特征词。
 * @param {string} text
 * @returns {string[]}
 */
export function tokenize(text) {
  const lower = (text || '').toLowerCase();
  const tokens = [];
  let latin = '';

  const flushLatin = () => {
    if (latin.length > 0) {
      if (!STOPWORDS.has(latin)) tokens.push(latin);
      latin = '';
    }
  };

  const chars = Array.from(lower);
  for (let i = 0; i < chars.length; i += 1) {
    const ch = chars[i];
    if (CJK.test(ch)) {
      flushLatin();
      if (!STOPWORDS.has(ch)) tokens.push(ch);
      const next = chars[i + 1];
      if (next && CJK.test(next)) {
        const bigram = ch + next;
        if (!STOPWORDS.has(bigram)) tokens.push(bigram);
      }
    } else if (/[a-z0-9]/.test(ch)) {
      latin += ch;
    } else {
      flushLatin();
    }
  }
  flushLatin();
  return tokens;
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

/** 逗号分隔的小数向量 -> seekdb VECTOR 字面量 */
export function toVectorLiteral(vector) {
  return `[${vector.map((v) => (Number.isFinite(v) ? v : 0).toFixed(6)).join(',')}]`;
}
