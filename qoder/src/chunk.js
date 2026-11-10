/**
 * chunk.js —— 把一份资料切成可检索的段落
 *
 * 切分策略：先按空行切段（Markdown 的自然段落），段落过长再按句号/问号/叹号切，
 * 最后用滑动窗口兜底，保证每块不超过 chunkSize 字并保留 overlap 上下文。
 */

/**
 * @param {string} text
 * @param {{chunkSize: number, chunkOverlap: number}} options
 * @returns {string[]}
 */
export function chunkText(text, { chunkSize, chunkOverlap }) {
  const normalized = (text || '').replace(/\r\n/g, '\n').trim();
  if (!normalized) return [];

  const paragraphs = normalized
    .split(/\n{2,}/)
    .map((part) => part.replace(/\s+/g, ' ').trim())
    .filter(Boolean);

  const pieces = [];
  for (const paragraph of paragraphs) {
    if (paragraph.length <= chunkSize) {
      pieces.push(paragraph);
      continue;
    }
    // 长段落：先按句子边界切，再按窗口兜底
    const sentences = paragraph
      .split(/(?<=[。！？!?；;.])/)
      .map((s) => s.trim())
      .filter(Boolean);
    let buffer = '';
    for (const sentence of sentences) {
      if (buffer.length + sentence.length + 1 <= chunkSize) {
        buffer = buffer ? `${buffer} ${sentence}` : sentence;
        continue;
      }
      if (buffer) pieces.push(buffer);
      if (sentence.length > chunkSize) {
        for (let i = 0; i < sentence.length; i += chunkSize - chunkOverlap) {
          pieces.push(sentence.slice(i, i + chunkSize));
        }
        buffer = '';
      } else {
        buffer = sentence;
      }
    }
    if (buffer) pieces.push(buffer);
  }

  // 合并过短的尾块，避免产生没有信息量的碎片
  const merged = [];
  for (const piece of pieces) {
    const last = merged[merged.length - 1];
    if (last && last.length + piece.length + 1 <= chunkSize) {
      merged[merged.length - 1] = `${last} ${piece}`;
    } else {
      merged.push(piece);
    }
  }
  return merged.filter(Boolean);
}

/**
 * 生成一个块的稳定 id：同一份资料的同一段永远得到同一个 id（幂等导入）。
 * @param {string} source
 * @param {number} index
 * @returns {string}
 */
export function chunkId(source, index) {
  let hash = 0x811c9dc5;
  const key = `${source}#${index}`;
  for (let i = 0; i < key.length; i += 1) {
    hash ^= key.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return `${source}::${index}::${hash.toString(16).padStart(8, '0')}`;
}
