/**
 * embed.test.js —— 分词、特征哈希向量与 VECTOR 字面量
 *
 * 数学部分直接测 src/embedders/local.js：它不读环境变量，结果可逐位复现。
 * 外部 provider（openai）要发网络请求，属于 selftest / 手工验证的范围，不在单测里。
 */
import assert from 'node:assert/strict';
import test from 'node:test';

import * as embedEntrypoint from '../src/embed.js';
import { buildIdf, embed, STOPWORDS, termFreq, tokenize } from '../src/embedders/local.js';

const { toVectorLiteral } = embedEntrypoint;

const DIM = 384;
const IDF = buildIdf(new Map([['向量', 8], ['检索', 2], ['混合', 4]]), 20);

function l2Norm(vector) {
  return Math.sqrt(vector.reduce((sum, value) => sum + value * value, 0));
}

test('中文切成「单字 + 相邻二元组」', () => {
  const tokens = tokenize('混合检索');
  assert.deepEqual(tokens, ['混', '混合', '合', '合检', '检', '检索', '索']);
  assert.ok(tokens.includes('混合'), '二元组要能表达中文短语');
});

test('英文按字母数字切分并转小写，词之间不产生二元组', () => {
  assert.deepEqual(tokenize('Hybrid Retrieval combines a lexical branch'), [
    'hybrid',
    'retrieval',
    'combines',
    'lexical',
    'branch',
  ]);
});

test('中英混排两种特征都保留', () => {
  const tokens = tokenize('HNSW 的 ef_search 参数');
  assert.deepEqual(tokens, ['hnsw', 'ef', 'search', '参', '参数', '数']);
  assert.ok(tokens.includes('hnsw'));
});

test('停用词不作为独立特征出现', () => {
  assert.deepEqual(tokenize('the and of'), [], '英文停用词全部被过滤');
  assert.ok(!tokenize('的字向量').includes('的'), '中文高频虚词被过滤');
  for (const stopword of ['the', '的', '我们', '一个']) {
    assert.ok(STOPWORDS.has(stopword), `${stopword} 应在停用词表里`);
    assert.ok(!tokenize(`${stopword} ${stopword}`).includes(stopword));
  }
});

test('termFreq 统计的是过滤后的特征词', () => {
  const tf = termFreq('向量检索 向量');
  assert.equal(tf.get('向量'), 2);
  assert.equal(tf.get('检索'), 1);
  assert.equal([...tf.values()].reduce((sum, count) => sum + count, 0), tokenize('向量检索 向量').length);
});

test('buildIdf：df 越大的词权重越低', () => {
  const idf = buildIdf(new Map([['常见词', 100], ['稀有词', 1]]), 100);
  assert.ok(idf.get('常见词') < idf.get('稀有词'));
  assert.equal(idf.get('常见词'), Math.log(101 / 101) + 1);
});

test('embed 输出维度正确且 L2 范数为 1', () => {
  for (const text of ['混合检索怎么融合两路结果', 'reciprocal rank fusion', 'EF_SEARCH']) {
    const vector = embed(text, IDF, DIM);
    assert.equal(vector.length, DIM);
    // 分量按 6 位小数取整（为了写进 seekdb 的 VECTOR 字面量），所以允许 1e-5 的偏差
    assert.ok(Math.abs(l2Norm(vector) - 1) < 1e-5, `${text} 的 L2 范数不是 1：${l2Norm(vector)}`);
  }
});

test('空文本得到零向量，不会除以 0', () => {
  const vector = embed('', IDF, DIM);
  assert.equal(vector.length, DIM);
  assert.equal(l2Norm(vector), 0);
  assert.ok(vector.every((value) => value === 0));
});

test('embed 是确定性的：同样输入永远同样向量', () => {
  assert.equal(embed('HNSW 参数怎么调', IDF, DIM).join(','), embed('HNSW 参数怎么调', IDF, DIM).join(','));
});

test('语义接近的文本比无关文本更相似', () => {
  const cosine = (a, b) => a.reduce((sum, value, i) => sum + value * b[i], 0);
  const query = embed('混合检索怎么融合两路结果', IDF, DIM);
  const related = embed('混合检索把关键词检索和语义检索两路结果融合排序', IDF, DIM);
  const unrelated = embed('读论文先看摘要和结论再看图', IDF, DIM);
  assert.ok(cosine(query, related) > cosine(query, unrelated));
});

test('toVectorLiteral：方括号 + 逗号分隔的 6 位小数', () => {
  assert.equal(toVectorLiteral([0, 0.5, -0.25]), '[0.000000,0.500000,-0.250000]');
  assert.equal(toVectorLiteral([Number.NaN, Number.POSITIVE_INFINITY]), '[0.000000,0.000000]');
  assert.equal(toVectorLiteral([]), '[]');

  const vector = embed('混合检索', IDF, 4);
  const literal = toVectorLiteral(vector);
  assert.match(literal, /^\[-?\d\.\d{6}(,-?\d\.\d{6}){3}\]$/, literal);
  assert.deepEqual(JSON.parse(literal), vector, '字面量要能原样读回同一个向量');
});

test('src/embed.js 仍然对外提供同一套导出名', () => {
  for (const name of ['embed', 'toVectorLiteral', 'tokenize', 'termFreq', 'buildIdf']) {
    assert.equal(typeof embedEntrypoint[name], 'function', `${name} 仍然是函数`);
  }
  assert.equal(embedEntrypoint.toVectorLiteral([0.5]), '[0.500000]');
  assert.equal(embedEntrypoint.tokenize, tokenize, '分词实现只有一份');
  assert.equal(embedEntrypoint.buildIdf, buildIdf);
  assert.equal(embedEntrypoint.termFreq, termFreq);
  assert.equal(embedEntrypoint.STOPWORDS, STOPWORDS);
});
