# 文献罗盘 PaperCompass · 端到端自测记录

- 执行时间：2026-11-10
- Node：v24.20.0
- seekdb：127.0.0.1:2881/papercompass
- 检索模式：native（sql 为等价降级路径），向量维度 384

## 1. 幂等导入

```
导入前段落数 = 8
首次导入后   = 8
重复导入后   = 8   （幂等：id 由 来源+段落序号 派生）
```

## 2. 检索：三种问法

```
$ 问：混合检索怎么融合两路结果
  模式=native  用时=8ms  命中 3 条
   - [both] rrf=0.03280 全文名次=1 向量名次=1  01-向量检索基础.md #0  向量检索基础笔记
     混合检索是把「关键词检索」和「语义检索」两路结果合并排序。最常见的融合方式是 RRF（Reciprocal Rank Fusion）：对每一...
   - [both] rrf=0.03230 全文名次=2 向量名次=2  selftest-sample.md #0  实验记录：HNSW 参数怎么调
     # 实验记录：HNSW 参数怎么调 在 10 万条 768 维向量上，ef_construction=200、ef_search=64 时，...
   - [both] rrf=0.03170 全文名次=3 向量名次=3  01-向量检索基础.md #1  向量检索基础笔记
     文档切块会显著影响检索质量。块太大，噪声多；块太小，上下文断裂，命中了也看不懂。常见做法是按自然段切，再加一点重叠。...

$ 问：HNSW 的 ef_search 调到多少合适
  模式=native  用时=6ms  命中 3 条
   - [both] rrf=0.03280 全文名次=1 向量名次=1  selftest-sample.md #0  实验记录：HNSW 参数怎么调
     # 实验记录：HNSW 参数怎么调 在 10 万条 768 维向量上，ef_construction=200、ef_search=64 时，...
   - [both] rrf=0.03230 全文名次=2 向量名次=2  01-向量检索基础.md #0  向量检索基础笔记
     混合检索是把「关键词检索」和「语义检索」两路结果合并排序。最常见的融合方式是 RRF（Reciprocal Rank Fusion）：对每一...
   - [both] rrf=0.03170 全文名次=3 向量名次=3  02-文献阅读与笔记方法.md #0  文献阅读与笔记方法
     读论文先看摘要和结论，再看图，再回头读方法。很多时间花在读引言上，但真正决定能不能复现的是方法与实验设置。 做笔记不要抄原文，要写「这句话回...

$ 问：reciprocal rank fusion
  模式=native  用时=5ms  命中 3 条
   - [both] rrf=0.03280 全文名次=1 向量名次=1  03-hybrid-retrieval-notes.md #0  Reading Notes on Hybrid Retrieval
     Hybrid retrieval combines a lexical branch with a semantic branch and ...
   - [both] rrf=0.03200 全文名次=2 向量名次=3  01-向量检索基础.md #0  向量检索基础笔记
     混合检索是把「关键词检索」和「语义检索」两路结果合并排序。最常见的融合方式是 RRF（Reciprocal Rank Fusion）：对每一...
   - [vector] rrf=0.01610 全文名次=- 向量名次=2  01-向量检索基础.md #1  向量检索基础笔记
     文档切块会显著影响检索质量。块太大，噪声多；块太小，上下文断裂，命中了也看不懂。常见做法是按自然段切，再加一点重叠。...

$ 问：EF_SEARCH
  模式=native  用时=5ms  命中 3 条
   - [both] rrf=0.03280 全文名次=1 向量名次=1  selftest-sample.md #0  实验记录：HNSW 参数怎么调
     # 实验记录：HNSW 参数怎么调 在 10 万条 768 维向量上，ef_construction=200、ef_search=64 时，...
   - [both] rrf=0.03180 全文名次=2 向量名次=4  01-向量检索基础.md #0  向量检索基础笔记
     混合检索是把「关键词检索」和「语义检索」两路结果合并排序。最常见的融合方式是 RRF（Reciprocal Rank Fusion）：对每一...
   - [vector] rrf=0.01610 全文名次=- 向量名次=2  03-hybrid-retrieval-notes.md #0  Reading Notes on Hybrid Retrieval
     Hybrid retrieval combines a lexical branch with a semantic branch and ...

```

## 3. 两条检索路径对照（同一查询、同一索引）

```
native 模式命中顺序：
  1. 01-向量检索基础.md #0  [both] 0.0328
  2. selftest-sample.md #0  [both] 0.0323
  3. 01-向量检索基础.md #1  [both] 0.0317
sql 模式命中顺序：
  1. 01-向量检索基础.md #0  [both] 0.03279
  2. selftest-sample.md #0  [both] 0.03226
  3. 01-向量检索基础.md #1  [both] 0.03175
两条路径重合条数 = 3/3
```

## 4. 索引确实被用上了（EXPLAIN）

```
  {"Query Plan":"========================================================================="}
  {"Query Plan":"|ID|OPERATOR         |NAME                        |EST.ROWS|EST.TIME(us)|"}
  {"Query Plan":"-------------------------------------------------------------------------"}
  {"Query Plan":"|0 |VECTOR INDEX SCAN|paper_doc(idx_paper_doc_emb)|76      |198         |"}
  {"Query Plan":"========================================================================="}
  {"Query Plan":"Outputs & filters:"}
  {"Query Plan":"-------------------------------------"}
  {"Query Plan":"  0 - output([paper_doc.id], [cosine_distance(paper_doc.emb, cast('[0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,"}
  {"Query Plan":"      0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,"}
  {"Query Plan":"      0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,"}
  {"Query Plan":"      0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,"}
  {"Query Plan":"      0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,"}
  {"Query Plan":"      0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0]', ARRAY(18, -1)))]), filter(nil), rowset=256"}
  {"Query Plan":"      access([paper_doc.__pk_increment], [paper_doc.emb], [paper_doc.id]), partitions(p0)"}
  {"Query Plan":"      is_index_back=true, is_global_index=false, "}
  {"Query Plan":"      range_key([paper_doc.__pk_increment], [paper_doc.__type_21_1794286248942956]), range(MIN,MIN ; MAX,MAX)always true"}
--- 全文索引 ---
  {"Query Plan":"============================================================================="}
  {"Query Plan":"|ID|OPERATOR             |NAME                        |EST.ROWS|EST.TIME(us)|"}
  {"Query Plan":"-----------------------------------------------------------------------------"}
  {"Query Plan":"|0 |SORT                 |                            |2       |448         |"}
  {"Query Plan":"|1 |└─TEXT RETRIEVAL SCAN|paper_doc(ft_paper_doc_body)|2       |447         |"}
  {"Query Plan":"============================================================================="}
  {"Query Plan":"Outputs & filters:"}
  {"Query Plan":"-------------------------------------"}
  {"Query Plan":"  0 - output([paper_doc.id]), filter(nil), rowset=16"}
  {"Query Plan":"      sort_keys([MATCH(paper_doc.body) AGAINST('HNSW'), DESC])"}
  {"Query Plan":"  1 - output([paper_doc.id], [MATCH(paper_doc.body) AGAINST('HNSW')]), filter(nil), rowset=16"}
  {"Query Plan":"      access([paper_doc.__pk_increment], [paper_doc.id]), partitions(p0)"}
  {"Query Plan":"      is_index_back=true, is_global_index=false, "}
  {"Query Plan":"      calc_relevance=true, match_expr(MATCH(paper_doc.body) AGAINST('HNSW')), "}
  {"Query Plan":"      pushdown_match_filter(MATCH(paper_doc.body) AGAINST('HNSW'))"}
```

```
paper_doc 的索引：
  uk_paper_doc_id  id  BTREE
  ft_paper_doc_body  body  FULLTEXT
  idx_paper_doc_emb  emb  VECTOR
```

## 5. 记忆链路

```
排入复习计划后，到期条目 = 0
  
打分 quality=4 -> ease=2.5 reps=2 间隔=3天 下次=2026-11-13 04:54:55
打分后仍然到期的条目 = 0（已排到未来）
检索足迹（paper_study，最近 3 条）：
  EF_SEARCH -> [vector] 0.01610
  EF_SEARCH -> [both] 0.03180
  EF_SEARCH -> [both] 0.03280
```

## 6. 概览

```
{
  "docs": 8,
  "terms": 715,
  "studies": 36,
  "reviews": 1,
  "dim": 384,
  "mode": "native"
}
```

## 7. 附录 · 单元测试（npm test）

- 执行时间：2026-11-13
- 命令：`npm test`（即 `node --test "test/*.test.js"`），Node v24.20.0
- 只依赖 Node 内置的 `node:test` / `node:assert`，**不需要 seekdb 实例**也能跑
- 覆盖：切块 `test/chunk.test.js` 9 条、分词与向量 `test/embed.test.js` 12 条、SM-2 排期 `test/review.test.js` 12 条，共 33 条全绿
- `src/review.js` 是从 `src/db.js` 的 `gradeReview()` 抽出的同一套数学：抽前抽后对 3456 组（难度系数 × 间隔 × 复习次数 × quality）逐一对齐，无差异

> 本节是手工追加的运行记录；重新执行 `npm run selftest` 会重写本文件，需要保留时先备份。

```text

> papercompass@1.0.0 test
> node --test "test/*.test.js"

✔ 空文本不产生任何块 (1.112ms)
✔ 按空行切段：两个自然段得到两个块 (0.7852ms)
✔ 短段落会被合并，避免产生没有信息量的碎片 (0.1169ms)
✔ CRLF 与换行噪音被归一化 (0.1572ms)
✔ 超长段落按句子边界切，每块都是完整句子 (0.2176ms)
✔ 句子拼装的块不会超过 chunkSize (0.127ms)
✔ 没有句子边界的超长文本走滑动窗口，overlap 生效 (0.2262ms)
✔ overlap 为 0 时窗口首尾相接、不重复 (0.1016ms)
✔ chunkId 对同样输入稳定，不同来源或段落号得到不同 id (0.2903ms)
✔ 中文切成「单字 + 相邻二元组」 (1.3563ms)
✔ 英文按字母数字切分并转小写，词之间不产生二元组 (0.164ms)
✔ 中英混排两种特征都保留 (0.1146ms)
✔ 停用词不作为独立特征出现 (0.1318ms)
✔ termFreq 统计的是过滤后的特征词 (0.1755ms)
✔ buildIdf：df 越大的词权重越低 (0.0909ms)
✔ embed 输出维度正确且 L2 范数为 1 (1.0943ms)
✔ 空文本得到零向量，不会除以 0 (0.1395ms)
✔ embed 是确定性的：同样输入永远同样向量 (0.2618ms)
✔ 语义接近的文本比无关文本更相似 (0.5755ms)
✔ toVectorLiteral：方括号 + 逗号分隔的 6 位小数 (0.3485ms)
✔ src/embed.js 仍然对外提供同一套导出名 (0.1003ms)
✔ 第一次记住（quality>=3）间隔 1 天 (1.8533ms)
✔ 第二次记住间隔 3 天 (0.1318ms)
✔ 第三次起间隔按 ease 递增：1 → 3 → 8 → 20 → 50 → 125 (0.2009ms)
✔ quality < 3 视为忘了：复习次数与间隔都归零 (0.1919ms)
✔ 重置后再答对，从头开始排间隔 (0.1571ms)
✔ 重置不惩罚难度系数，ease 保持原值 (0.1428ms)
✔ quality=5 提高 ease，quality=3 压低 ease (0.3852ms)
✔ ease 有 1.3 的下限，连续低分也不会跌破 (0.3758ms)
✔ 难度系数很低时间隔仍然至少 1 天 (0.286ms)
✔ quality 超出 0-5 会被夹回区间内 (0.3594ms)
✔ 表里没有这一行时用初始状态起步 (0.2129ms)
✔ 打分不会改写传进来的行（纯函数） (0.1238ms)
ℹ tests 33
ℹ suites 0
ℹ pass 33
ℹ fail 0
ℹ cancelled 0
ℹ skipped 0
ℹ todo 0
ℹ duration_ms 103.235
```
