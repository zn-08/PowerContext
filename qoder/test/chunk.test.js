/**
 * chunk.test.js —— 段落切块
 *
 * 运行：npm test
 */
import assert from 'node:assert/strict';
import test from 'node:test';

import { chunkId, chunkText } from '../src/chunk.js';

const OPTIONS = { chunkSize: 30, chunkOverlap: 10 };

function everyPieceFits(pieces, chunkSize) {
  for (const piece of pieces) {
    assert.ok(piece.length <= chunkSize, `块长度 ${piece.length} 超过 ${chunkSize}：${piece}`);
  }
}

test('空文本不产生任何块', () => {
  assert.deepEqual(chunkText('', OPTIONS), []);
  assert.deepEqual(chunkText('   \n\n  \t ', OPTIONS), []);
  assert.deepEqual(chunkText(null, OPTIONS), []);
});

test('按空行切段：两个自然段得到两个块', () => {
  const first = '第一段讲混合检索的融合方式与排名';
  const second = '第二段讲向量索引的参数调优方法';
  const pieces = chunkText(`${first}\n\n${second}`, { chunkSize: 30, chunkOverlap: 5 });
  assert.deepEqual(pieces, [first, second]);
});

test('短段落会被合并，避免产生没有信息量的碎片', () => {
  const pieces = chunkText('甲段很短\n\n乙段也很短', { chunkSize: 60, chunkOverlap: 5 });
  assert.deepEqual(pieces, ['甲段很短 乙段也很短']);
});

test('CRLF 与换行噪音被归一化', () => {
  const pieces = chunkText('甲段内容\r\n\r\n乙段内容', { chunkSize: 60, chunkOverlap: 5 });
  assert.deepEqual(pieces, ['甲段内容 乙段内容']);
  assert.ok(!pieces[0].includes('\r'), '块里不应该残留 \\r');
});

test('超长段落按句子边界切，每块都是完整句子', () => {
  const paragraph = '第一句话说明背景。第二句话给出方法。第三句话写出结论。第四句话补充限制。';
  const chunkSize = 13;
  const pieces = chunkText(paragraph, { chunkSize, chunkOverlap: 3 });
  assert.deepEqual(pieces, [
    '第一句话说明背景。',
    '第二句话给出方法。',
    '第三句话写出结论。',
    '第四句话补充限制。',
  ]);
  for (const piece of pieces) assert.ok(piece.endsWith('。'), `句子被切断了：${piece}`);
  everyPieceFits(pieces, chunkSize);
});

test('句子拼装的块不会超过 chunkSize', () => {
  const paragraph = '第一句话说明背景。第二句话给出方法。第三句话写出结论。第四句话补充限制。';
  const pieces = chunkText(paragraph, { chunkSize: 30, chunkOverlap: 5 });
  assert.ok(pieces.length > 1, '超长段落必须切成多块');
  everyPieceFits(pieces, 30);
});

test('没有句子边界的超长文本走滑动窗口，overlap 生效', () => {
  const text = `${'a'.repeat(20)}${'b'.repeat(20)}${'c'.repeat(20)}`;
  const { chunkSize, chunkOverlap } = { chunkSize: 20, chunkOverlap: 10 };
  const pieces = chunkText(text, { chunkSize, chunkOverlap });
  assert.deepEqual(pieces.slice(0, 3), ['a'.repeat(20), 'a'.repeat(10) + 'b'.repeat(10), 'b'.repeat(20)]);
  // 相邻块之间重叠 chunkOverlap 个字，上下文才不会断
  for (let i = 0; i < 3; i += 1) {
    assert.equal(pieces[i].slice(chunkOverlap), pieces[i + 1].slice(0, chunkSize - chunkOverlap));
  }
  everyPieceFits(pieces, chunkSize);
});

test('overlap 为 0 时窗口首尾相接、不重复', () => {
  const text = 'x'.repeat(40);
  const pieces = chunkText(text, { chunkSize: 20, chunkOverlap: 0 });
  assert.deepEqual(pieces, ['x'.repeat(20), 'x'.repeat(20)]);
});

test('chunkId 对同样输入稳定，不同来源或段落号得到不同 id', () => {
  assert.equal(chunkId('01-notes.md', 0), chunkId('01-notes.md', 0));
  assert.notEqual(chunkId('01-notes.md', 0), chunkId('01-notes.md', 1));
  assert.notEqual(chunkId('01-notes.md', 0), chunkId('02-notes.md', 0));
  assert.match(chunkId('01-notes.md', 3), /^01-notes\.md::3::[0-9a-f]{8}$/);
});
