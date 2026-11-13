/**
 * tokenize.js —— 分词与停用词（前后端共用的纯函数）
 *
 * 这个文件是**检索口径的唯一事实来源**：
 *   · 后端 src/embedders/local.js 用它算特征哈希向量的词袋；
 *   · 前端 public/app.js 通过 /tokenize.js 加载同一个文件，决定片段里高亮哪些命中位置。
 * 所以界面上标黄的词，就是数据库那一路真正参与打分的词——不会出现「看着命中其实没命中」。
 *
 * 因此这里必须保持零依赖、不碰 Node API，浏览器和 Node 都能直接 import。
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
