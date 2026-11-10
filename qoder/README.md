# 文献罗盘 PaperCompass · Qoder × seekdb

> 把论文、课程笔记、实验记录丢进来，用一句自然语言问「混合检索怎么融合两路结果」，
> 立刻定位到段落与出处；顺手把这篇排进复习计划，下次打开还记得该复习什么。

**检索与记忆全部落在 seekdb 里**：全文走 `FULLTEXT(ik)` 索引，向量走 `VECTOR(HNSW, cosine)` 索引，
两路结果由数据库用 RRF 融合（`DBMS_HYBRID_SEARCH`），客户端不拼合；检索足迹与复习计划存在表里。

---

## 一、解决的痛点

| 痛点 | 本项目的做法 |
| --- | --- |
| 笔记散在十几个文件里，记不清关键词时搜索框完全用不上 | 段落级切块 + 全文/向量**混合检索**，用自己的措辞也能命中 |
| 抄原文的笔记检索不到（提问用自己的词，笔记用作者的词） | 向量分支补语义泛化，命中方式会标出是「全文」「向量」还是「两者」 |
| 读完就忘，没人提醒 | 检索过的段落可一键排进**复习计划**，按 SM-2 简化算法给出下次复习时间 |

## 二、核心功能

1. **导入**：粘贴 Markdown / 纯文本 → 按自然段落切块（带重叠）→ 写入 seekdb。
2. **混合检索**：一句话提问 → 全文（ik）+ 向量（HNSW）两路召回 → 库内 RRF 融合 → 返回段落、出处、命中方式与名次。
3. **检索足迹即记忆**：每次检索的提问、命中段落、分数写入 `paper_study` 表。
4. **复习计划**：`paper_review` 表记录难度系数、复习次数与下次到期时间，界面里「今天该复习的」直接读它。
5. **静态展示站**：`site/` 是可直接发布到 Qoder Sites 的展示页，演示数据来自真实 seekdb 运行快照。

## 三、技术结合：seekdb 具体做了什么

| 能力 | 用在哪 | 怎么核对 |
| --- | --- | --- |
| 全文索引（`ik` 分词） | 段落正文 `MATCH(body) AGAINST (... IN NATURAL LANGUAGE MODE)` | `SHOW INDEX FROM paper_doc` → `ft_paper_doc_body` |
| 向量索引（HNSW / cosine） | 段落向量 `emb VECTOR(384)`；查询向量按 IDF 加权 | `EXPLAIN` → `VECTOR INDEX SCAN paper_doc(idx_paper_doc_emb)` |
| 混合检索 | `DBMS_HYBRID_SEARCH.GET_SQL()` 生成单条 SQL，RRF 在库内融合 | 界面「本次执行的 SQL」折叠区；`evidence/selftest.md` 第 3 节两条路径对照 |
| 词表与 IDF | `paper_term` / `paper_doc_term`，`df` 由 SQL 聚合统计 | `SELECT term, df FROM paper_term` |
| 记忆表 | `paper_study`（检索足迹）、`paper_review`（复习计划） | `evidence/selftest.md` 第 5 节 |

**没有用什么**：没有在客户端把全文结果和向量结果各取一遍再自己拼合——融合由数据库完成；
没有把检索逻辑放在内存里做——命中结果全部来自 seekdb 的索引扫描。

### 数据模型

```sql
paper_doc       (id, title, source, chunk_index, body, emb VECTOR(384), updated_at)
                UNIQUE(id) + FULLTEXT(body, ik) + VECTOR INDEX(emb, hnsw/cosine)
paper_doc_term  (doc_id, term, tf)            -- 段落级词频
paper_term      (term, df)                     -- 词表与文档频率（IDF 来源）
paper_study     (user_id, doc_id, title, query_text, hit_by, score, created_at)  -- 检索足迹
paper_review    (user_id, doc_id, title, ease, interval_days, reps, due_at, updated_at) -- 复习计划
```

### 向量方案

`src/embed.js` 用**完全本地的特征哈希 TF-IDF 向量**：

- 分词：拉丁词按字母数字切分；中文切成「单字 + 相邻二元组」；
- 哈希：FNV-1a 映射到 384 维并带正负号（sign hashing）抑制冲突偏差；
- 加权：TF 用 sublinear（`1 + ln tf`），**IDF 来自 seekdb 的 `paper_term` 表**；
- 归一化：L2 归一化，因此可以直接用 `cosine_distance`。

这样评审环境**不需要模型下载、不需要 API Key、不需要 GPU**，`npm install` 之后就能跑。
需要更强的语义能力时，把 `embed()` 换成外部 embedding 即可，其余流程不变。

## 四、目录结构

```
qoder/
├── README.md                     本文件
├── QODER-DEV.md                  Qoder 开发记录（含给 Qoder 的开发任务清单与 Sites 发布指引）
├── package.json
├── src/
│   ├── config.js                 环境变量配置
│   ├── embed.js                  本地特征哈希 TF-IDF 向量
│   ├── chunk.js                  段落切块
│   ├── db.js                     seekdb 访问层：建表 / 导入 / 混合检索 / 记忆表
│   └── server.js                 HTTP 服务 + 静态前端
├── public/                       应用前端（检索 / 导入 / 复习）
├── scripts/
│   ├── setup-db.js               建库建表 + 灌示例语料
│   ├── selftest.js               端到端自测（产出 evidence/selftest.md）
│   ├── build-site.js             生成静态站点快照
│   └── serve-site.js             本地预览静态站点
├── data/notes/                   示例语料
├── site/                         可发布到 Qoder Sites 的静态展示站
└── evidence/
    ├── selftest.md               端到端自测的真实记录
    └── screenshots/              界面截图
```

## 五、运行说明

### 1. 起一个 seekdb

```bash
docker run -d --name papercompass-seekdb -p 2881:2881 oceanbase/seekdb:latest
# 或用本地二进制：./seekdb --port=2881 --nodaemon
mysql -h127.0.0.1 -P2881 -uroot -e "SELECT VERSION()"
```

### 2. 装依赖 + 建库 + 灌示例语料

```bash
cd qoder
npm install
export SEEKDB_HOST=127.0.0.1 SEEKDB_PORT=2881 SEEKDB_USER=root SEEKDB_PASSWORD=''
npm run setup-db
```

预期输出：

```
[setup] 01-向量检索基础.md: 2 chunks (7 vectors, 645 terms)
[setup] 02-文献阅读与笔记方法.md: 1 chunks (7 vectors, 645 terms)
[setup] 03-hybrid-retrieval-notes.md: 4 chunks (7 vectors, 645 terms)
[setup] total chunks = 7
```

### 3. 起服务

```bash
npm start
# 打开 http://127.0.0.1:5174
```

### 4. 端到端自测（同时产出验证记录）

```bash
npm run selftest
# 产出 evidence/selftest.md
```

### 5. 本地预览展示站

```bash
npm run build-site   # 生成 site/snapshot.json（真实运行快照）
node scripts/serve-site.js 5175
# 打开 http://127.0.0.1:5175
```

### 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `SEEKDB_HOST` / `SEEKDB_PORT` | `127.0.0.1` / `2881` | seekdb 地址 |
| `SEEKDB_USER` / `SEEKDB_PASSWORD` | `root` / 空 | 账号 |
| `SEEKDB_DATABASE` | `papercompass` | 库名（不存在会自动建） |
| `PORT` | `5174` | HTTP 端口 |
| `EMBED_DIM` | `384` | 向量维度（必须与建表时的 `VECTOR(384)` 一致） |
| `CHUNK_SIZE` / `CHUNK_OVERLAP` | `320` / `80` | 切块长度与重叠 |
| `SEARCH_MODE` | `native` | `native` = `DBMS_HYBRID_SEARCH`；`sql` = 手写单条 RRF（等价降级路径） |
| `TOP_K` | `8` | 默认返回条数 |

### HTTP 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 服务与连接状态 |
| GET | `/api/stats` | 段落数、词表数、足迹数、复习数 |
| GET | `/api/docs` | 已入库段落列表 |
| POST | `/api/ingest` | `{title, source, text, userId}` → 切块入库 |
| POST | `/api/search` | `{query, topK, mode, userId}` → 混合检索结果 + 实际 SQL |
| GET | `/api/review/due?user=` | 到期的复习条目 |
| POST | `/api/review/schedule` | 把段落排进复习计划 |
| POST | `/api/review/grade` | `{userId, docId, quality}` → SM-2 打分 |

## 六、验证样例

> 下面每一步都在本机真实跑过，完整记录见 [`evidence/selftest.md`](evidence/selftest.md)。

### 样例 1 · 导入（幂等）

```bash
npm run setup-db
npm run setup-db      # 再跑一次，段落数不变
```

```
导入前段落数 = 8
首次导入后   = 8
重复导入后   = 8   （幂等：id 由 来源+段落序号 派生）
```

### 样例 2 · 中文语义问句

输入：`混合检索怎么融合两路结果`

```
[both] rrf=0.03280 全文名次=1 向量名次=1  01-向量检索基础.md #0  向量检索基础笔记
[both] rrf=0.03230 全文名次=2 向量名次=2  selftest-sample.md #0  实验记录：HNSW 参数怎么调
[both] rrf=0.03170 全文名次=3 向量名次=3  01-向量检索基础.md #1  向量检索基础笔记
```

### 样例 3 · 中英混排

输入：`HNSW 的 ef_search 调到多少合适`

```
[both] rrf=0.03280 全文名次=1 向量名次=1  selftest-sample.md #0  实验记录：HNSW 参数怎么调
[both] rrf=0.03230 全文名次=2 向量名次=2  01-向量检索基础.md #0  向量检索基础笔记
[both] rrf=0.03170 全文名次=3 向量名次=3  02-文献阅读与笔记方法.md #0  文献阅读与笔记方法
```

### 样例 4 · 英文术语

输入：`reciprocal rank fusion`

```
[both] rrf=0.03280 全文名次=1 向量名次=1  03-hybrid-retrieval-notes.md #0  Reading Notes on Hybrid Retrieval
[both] rrf=0.03200 全文名次=2 向量名次=3  01-向量检索基础.md #0  向量检索基础笔记
[vector] rrf=0.01610 全文名次=- 向量名次=2  01-向量检索基础.md #1  向量检索基础笔记
```

> 第三条只被向量分支召回——这正是混合检索存在的意义：全文没命中「切块」但语义相关。

### 样例 5 · 记忆链路

```
排入复习计划后，到期条目 = 1
  01-向量检索基础.md::0::84ff3cff (向量检索基础笔记)
打分 quality=4 -> ease=2.5 reps=1 间隔=1天 下次=2026-11-11 04:54:29
打分后仍然到期的条目 = 0（已排到未来）
检索足迹（paper_study，最近 3 条）：
  EF_SEARCH -> [vector] 0.01610
  EF_SEARCH -> [both] 0.03180
  EF_SEARCH -> [both] 0.03280
```

## 七、已知限制

- 向量是**本地特征哈希 TF-IDF**，不是语义模型：对同义改写有效，对完全不同的表述不如真实 embedding 模型。
  需要更强语义时替换 `src/embed.js` 的 `embed()` 即可（注意维度要一致）。
- `paper_doc` **不使用用户主键**（seekdb 原生混合检索要求隐藏 `__pk_increment` 列），
  因此幂等性靠 `UNIQUE(id)` 保证，删除段落需要单独执行 `DELETE`。
- 混合检索的 `native` 模式依赖 `DBMS_HYBRID_SEARCH`（seekdb ≥ 1.3）；
  老版本会自动回退到手写 RRF 的 `sql` 模式，两条路径在 `evidence/selftest.md` 第 3 节做过一致性对照。
- 服务没有鉴权，面向本地/评审环境使用；多副本部署需要自己加。
