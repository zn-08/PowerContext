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
