# 技术说明：阶跃星辰 · 端侧 AI 推理性能优化与 seekdb 混合检索

> 全部数值均为本机实测。环境：Windows 10.0.26200 x64 / Node v24.20.0 / npm 11.19.0 /
> seekdb `5.7.25-OceanBase seekdb-v1.3.0.0` @ `127.0.0.1:2881` / Chrome 155.0.8059.85 (headless)。
> 计时范围与设备见 §5；重复测试取**中位数**，「首次」与「预热后」分开列出。

---

## 1. 模型与量化

| 项 | 值 |
| --- | --- |
| 模型来源 | HuggingFace `Xenova/all-MiniLM-L6-v2`（默认）/ `Xenova/paraphrase-multilingual-MiniLM-L12-v2`（可选） |
| 结构 | MiniLM 6 层 / 12 层，12 头，隐层 384，**输出向量 384 维**（L2 归一化，mean pooling） |
| 许可 | Apache-2.0，可离线分发 |
| 量化方式 | **INT8 动态量化**（`onnx/model_quantized.onnx`，Transformers.js `dtype:'q8'`）；另提供 FP16（`onnx/model_fp16.onnx`）与 FP32 基线（`onnx/model.onnx`） |
| 推理运行时 | ONNX Runtime Web（随 `@huggingface/transformers@3.7.6` 提供），wasm 与 wasm 文件全部来自**本机** `/vendor/ort/`，不访问 CDN |

**体积对比（ONNX 权重，实测字节数）**

| 模型 | FP32 | FP16 | INT8 | INT8/FP32 | 词表+配置 |
| --- | --- | --- | --- | --- | --- |
| all-MiniLM-L6-v2 | 86.20 MB | 43.20 MB (50.1%) | **21.91 MB (25.4%)** | **压缩比 3.93×** | 0.68 MB |
| paraphrase-multilingual-MiniLM-L12-v2 | 448.48 MB | 224.43 MB (50.0%) | **112.83 MB (25.2%)** | **压缩比 3.97×** | 16.29 MB |

**模型下载耗时**（`scripts/fetch-model.js`，经本机 HTTP 代理，实测带宽约 1.1 MB/s）

| 文件 | all-MiniLM-L6-v2 | paraphrase-multilingual-MiniLM-L12-v2 |
| --- | --- | --- |
| `config.json` | 0.93 s | 0.78 s |
| `tokenizer.json` | 2.90 s | 14.26 s |
| `tokenizer_config.json` / `special_tokens_map.json` | 0.85 s / 0.79 s | 0.90 s / 0.86 s |
| `onnx/model.onnx` (FP32) | 53.35 s | 278.30 s |
| `onnx/model_fp16.onnx` | 41.36 s | 104.84 s |
| `onnx/model_quantized.onnx` (INT8) | **21.04 s** | **53.41 s** |

> 离线部署时上述下载全部不需要发生：把 `models/<org>/<repo>/` 目录整体拷贝到目标机器即可
> （Transformers.js 解析规则为 `<localModelPath>/<model_id>/<file>`，本项目 `localModelPath = '/models/'`）。

---

## 2. 端侧运行与降级

### 2.1 计算位置

- **文档向量与查询向量 100% 在浏览器内计算**：`public/app.js` 用
  `pipeline('feature-extraction', modelId, { device, dtype })` 得到 extractor，
  再以 `{ pooling: 'mean', normalize: true }` 输出 384 维 L2 归一化向量。
- 后端 `server/` **不 import 任何模型库**（`server/` 下没有 `@huggingface/transformers` 的引用），
  只接收浏览器算好的向量并写入 seekdb。**无任何云端推理 API 调用。**

### 2.2 WebGPU → WASM 自动降级（实测踩坑与修复）

首版实现按「WebGPU 失败就试 WASM」逐候选重试，在真实浏览器里**降级失败**。根因与修复：

| 现象 | 根因 | 修复 |
| --- | --- | --- |
| `device:'wasm'` 也报 `no available backend found. ERR: [webgpu] Error: Failed to get GPU adapter` | **一次失败的 WebGPU 初始化会污染 ONNX Runtime Web 的全局后端注册表**，后续 WASM 会话继承同一个错误 | ① 先 `await navigator.gpu.requestAdapter()`，**返回 null 就不尝试 WebGPU**（`probeWebGPU()`）；② 若 WebGPU 仍失败，用带 cache-buster 的 `import()` 重新加载模块拿全新 ORT 实例后再降级（`resetTransformersModule()`） |

> 复现脚本：`scripts/probe-ort.js`。实测：`navigator.gpu` 存在但 `requestAdapter()` 返回 `null` 时，
> 「先 wasm」→ wasm **成功**；「先 webgpu」→ webgpu 与 wasm **双双失败**。修复后两者都正确。

候选优先级：`webgpu+请求档位 → webgpu+fp16 → webgpu+fp32 → wasm+请求档位 → wasm+q8 → wasm+fp32`。
界面顶部徽章与左侧「端侧推理」面板同时显示**当前后端、实际生效的量化档位、降级原因、模型体积、加载耗时**。

### 2.3 实测（本机为无独显服务器，`requestAdapter()` 返回 `null`，故全部走 WASM）

| 指标 | 实测 |
| --- | --- |
| 推理后端 | **WASM**（降级原因：`navigator.gpu.requestAdapter()` 返回 null） |
| INT8 权重体积 | 21.91 MB（从本机 `/models/` 加载，无外网请求） |
| 模型加载耗时（浏览器，WASM） | 首次 746–943 ms（多次运行 669–943 ms） |
| 文档向量化吞吐（浏览器，WASM，24 篇） | 8.78 篇/秒（中位数；多次运行 8.8–11.1 篇/秒） |
| 查询向量化（浏览器，WASM） | 14.0–18.6 ms |

---

## 3. seekdb 数据层与混合检索

### 3.1 接入方式

**官方 npm SDK `seekdb@1.4.0` 的 Server 模式**（纯 JS，底层 `mysql2`，Windows 可用；原生运行时包只提供 linux/darwin）：

```js
import { SeekdbClient, SeekdbAdminClient } from 'seekdb';

const admin = new SeekdbAdminClient({ host, port, user, password });
await admin.createDatabase('stepfun_kb');            // 库不存在则创建

const client = new SeekdbClient({ host, port, user, password, database: 'stepfun_kb' });
await client.execute('SELECT 1');                    // 原生 SQL 执行
```

用到的 SDK 能力：`SeekdbAdminClient.createDatabase/close`、`SeekdbClient.execute(sql, params)`。
混合检索用到的会话变量 `SET @search_parm` 需要**连接亲和性**，因此这一段用 SDK 同款驱动 `mysql2`
的 `pool.getConnection()` 固定同一条物理连接执行（`server/db.js`）。

### 3.2 表结构（向量 + 原文 + 元数据全部落在 seekdb）

```sql
CREATE TABLE `stepfun_docs` (
  id         VARCHAR(64)   NOT NULL,
  title      VARCHAR(512)  NOT NULL,
  body       MEDIUMTEXT    NOT NULL,          -- title + 原文，全文索引列
  source     VARCHAR(256)  NOT NULL DEFAULT '',
  meta       JSON          DEFAULT NULL,      -- 元数据
  emb        VECTOR(384)   DEFAULT NULL,      -- 端侧生成的向量
  updated_at DATETIME(3)   NOT NULL,
  UNIQUE KEY uk_stepfun_docs_id (id),
  FULLTEXT INDEX ft_stepfun_docs_body (body) WITH PARSER ik,
  VECTOR   INDEX idx_stepfun_docs_emb (emb) WITH (distance = cosine, type = hnsw, lib = vsag)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

两处关键设计（均由实测反推得出，见 `scripts/probes/probe-shape.js`）：

1. **不使用用户自定义主键**。OceanBase 原生混合检索 `DBMS_HYBRID_SEARCH` 要求表使用隐藏
   `__pk_increment` 列，遇到用户主键会直接报
   `table with user provided primary key not supported`。改用 `UNIQUE KEY` 后既保留幂等 upsert
   （`ON DUPLICATE KEY UPDATE`）又满足原生混合检索要求。
2. **FULLTEXT 索引必须是单列**。`FULLTEXT (title, doc_text)` 会被原生混合检索拒绝
   （`Can't find FULLTEXT index matching the column list`），因此把标题与正文拼成一个 `body` 列建索引。
   分词器用 `ik`（`ik_mode=smart`），中英文均可命中。

### 3.3 ★ 混合检索：一条同时含全文候选与向量候选的 SQL

**主路径 = OceanBase 原生 `DBMS_HYBRID_SEARCH`**：由数据库自己生成这条 SQL，客户端只提交
「查询文本 + 端侧算好的查询向量」，**不做任何两路结果的拼合**。

```js
// server/db.js
const parm = {
  query: { query_string: { fields: ['body'], query: qtext } },        // 全文条件
  knn:   { field: 'emb', k: topK * 2, query_vector: qvec },          // 向量条件
  rank:  { rrf: { rank_window_size: 50, rank_constant: 60 } },       // RRF 融合
  size:  topK,
};
await conn.query('SET @search_parm = ?', [JSON.stringify(parm)]);
const [g] = await conn.query(`SELECT DBMS_HYBRID_SEARCH.GET_SQL('stepfun_docs', @search_parm) AS query_sql FROM dual`);
const [rows] = await conn.query(g[0].query_sql);   // ← 数据库生成的混合检索 SQL，在此执行
```

数据库生成的 SQL（**原文照抄**，仅把 384 维向量字面量缩写）：

```sql
SELECT ifnull(`_fts`.`id`, `_vs`.`id`) as `id`,
       ifnull(`_fts`.`title`, `_vs`.`title`) as `title`,
       ifnull(`_fts`.`body`, `_vs`.`body`) as `body`,
       ifnull(`_fts`.`source`, `_vs`.`source`) as `source`,
       ifnull(`_fts`.`meta`, `_vs`.`meta`) as `meta`,
       ifnull(`_fts`.`emb`, `_vs`.`emb`) as `emb`,
       ifnull(`_fts`.`updated_at`, `_vs`.`updated_at`) as `updated_at`,
       (ifnull(1 / (`_fts`.`_keyword_rank` + 60), 0)
      + ifnull(1 / (`_vs`.`_semantic_rank` + 60), 0)) as `_score`,
       _fts._keyword_rank as ft_rank, _vs._semantic_rank as vec_rank
FROM (
  ( SELECT *, RANK() over( ORDER BY `_keyword_score` DESC) as `_keyword_rank`
    FROM ( SELECT /*+ opt_param('hidden_column_visible', 'true') */
                 `__pk_increment`, `id`, `title`, `body`, `source`, `meta`, `emb`, `updated_at`,
                 match(`body`) against('端侧模型量化能省多少体积' in natural language mode) as `_keyword_score`
           FROM `stepfun_kb`.`stepfun_docs`
           WHERE match(`body`) against('端侧模型量化能省多少体积' in natural language mode)
           ORDER BY `_keyword_score` DESC LIMIT 1000
         ) `_fts`
  full join
  ( SELECT *, RANK() over( ORDER BY `_semantic_score` DESC) as `_semantic_rank`
    FROM ( SELECT /*+ opt_param('hidden_column_visible', 'true') */
                 cosine_distance(`emb`, '[0.0131, -0.0264, …]') as `_distance`,
                 `__pk_increment`, `id`, `title`, `body`, `source`, `meta`, `emb`, `updated_at`,
                 round(1 - cosine_distance(`emb`, '[0.0131, -0.0264, …]') / 2, 8) as `_semantic_score`
           FROM `stepfun_kb`.`stepfun_docs`
           ORDER BY `_distance` APPROXIMATE LIMIT 6
         ) `_vs`
  on `_fts`.`__pk_increment` = `_vs`.`__pk_increment`
) ORDER BY `_score` DESC, ifnull(`_vs`.`__pk_increment`, `_fts`.`__pk_increment`) LIMIT 10
```

- **全文候选**：`match(body) against(... in natural language mode)` → 命中 `FULLTEXT(ik)` 索引
- **向量候选**：`ORDER BY _distance APPROXIMATE LIMIT k` → 命中 `VECTOR(HNSW)` 索引
- **融合**：两路各自 `RANK()`，再用 RRF `1/(rank+60)` 相加 —— **融合在数据库内部完成**
- 末尾 `, _fts._keyword_rank as ft_rank, _vs._semantic_rank as vec_rank` 是本项目在
  **不改变语义**的前提下追加的两列（`server/db.js` 的 `exposeBranchRanks()`），
  仅用于在界面上标出「命中方式（全文 / 向量 / 两者）」。

> `APPROXIMATE` 关键字是 seekdb 的关键：不加它，优化器会退化成 `TABLE FULL SCAN`；
> 加上才走 `VECTOR INDEX SCAN`（实测 EXPLAIN 验证，见 §5.4）。

**备选路径 = 手写单条 RRF SQL**（`server/db.js` 的 `HYBRID_SQL`，界面可切换，用于交叉验证）：

```sql
SELECT s.id, s.title, LEFT(s.body,400) AS snippet, s.source, s.updated_at,
       s.vec_dist, s.vec_rank, s.ft_score, s.ft_rank,
       ROUND(1.0/(60+s.vec_rank) + COALESCE(1.0/(60+s.ft_rank),0), 8) AS rrf_score,
       CASE WHEN s.vec_rank IS NOT NULL AND s.ft_rank IS NOT NULL THEN 'both'
            WHEN s.vec_rank IS NOT NULL THEN 'vector' ELSE 'fulltext' END AS hit_by
FROM (
  SELECT id, title, body, source, updated_at, vec_dist, ft_score, vec_rank,
         CASE WHEN ft_score > 0 THEN ROW_NUMBER() OVER (ORDER BY ft_score DESC, id ASC) ELSE NULL END AS ft_rank
  FROM (
    SELECT id, title, body, source, updated_at,
           cosine_distance(emb, ?) AS vec_dist,
           MATCH(body) AGAINST (? IN NATURAL LANGUAGE MODE) AS ft_score,
           ROW_NUMBER() OVER (ORDER BY cosine_distance(emb, ?) ASC, id ASC) AS vec_rank
    FROM `stepfun_docs`
  ) vec_candidates
) s
ORDER BY rrf_score DESC, s.vec_rank ASC, s.updated_at DESC
LIMIT ?;
```

两条路径在实测中返回**同序同分**（RRF k=60），可交叉验证。

### 3.4 向量 / 原文 / 元数据确实存在 seekdb 里

抽样行（`SELECT id, title, LENGTH(body), JSON_EXTRACT(meta,'$.topic') FROM stepfun_docs LIMIT 1`）：

```
{"id":"d01","title":"seekdb 原生混合检索 DBMS_HYBRID_SEARCH","bl":464,"topic":"hybrid"}
```

---

## 4. 状态、刷新与增量导入

| 能力 | 实现 | 实测 |
| --- | --- | --- |
| 增量导入 | `INSERT ... ON DUPLICATE KEY UPDATE`（`UNIQUE KEY uk_..._id`） | 同 id 覆盖写入，幂等 |
| 索引刷新 | 显式 `COMMIT`；并尝试 `ALTER TABLE ... REFRESH FULLTEXT INDEX`（本版本不支持，退回 commit） | 刷新耗时中位数 **2.1–4.6 ms** |
| 刷新后可见性 | seekdb 的 FULLTEXT 索引在事务提交后生效 | 增量文档**立即**被检索到（浏览器端与 Node 侧均验证） |
| 集合状态 | `GET /api/status` 返回 `{connected, version, database, table, docCount}` | 界面「seekdb 状态」卡片实时显示 |
| 会话状态 | 服务端维护 `session.id / startedAt / lastImportAt / lastQueryAt / lastDevice / lastDtype / importedDocs / queryCount` | `GET /api/status` 返回，界面显示会话 ID |
| 前端会话态 | 页面内保留当前模型、后端、量化档位、导入批次；`清除集合` 按钮 + `清空集合` API | — |

---

## 5. 实测数据

### 5.1 Node 侧分阶段计时（onnxruntime-node / CPU，24 篇语料，9 条查询）

| 模型 | 量化 | 加载(冷) | 文档向量化 首次/中位数 | 吞吐 | 写入 | 索引刷新 | 查询向量化 首次/中位数 | seekdb 检索 首次/中位数 | 端到端 首次/中位数 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| all-MiniLM-L6-v2 | **INT8** | 177.73 ms | 155.14 / **179.22 ms** | **133.92 篇/s** | 182.40 ms | 3.40 ms | 2.61 / **3.53 ms** | 6.64 / **8.95 ms** | 9.56 / **12.56 ms** |
| all-MiniLM-L6-v2 | FP16 | 200.80 ms | 448.45 / 466.30 ms | 51.47 篇/s | 113.63 ms | 2.93 ms | 8.76 / 12.51 ms | 7.49 / 9.48 ms | 16.55 / 22.43 ms |
| all-MiniLM-L6-v2 | FP32 | 212.57 ms | 313.77 / 360.88 ms | 66.50 篇/s | 152.75 ms | 4.25 ms | 3.14 / 4.57 ms | 7.47 / 9.48 ms | 11.08 / 14.28 ms |
| paraphrase-multilingual-L12-v2 | **INT8** | 993.14 ms | 296.08 / **312.80 ms** | 76.73 篇/s | 216.31 ms | 3.56 ms | 4.43 / **6.71 ms** | 7.59 / **9.35 ms** | 12.70 / **16.61 ms** |
| paraphrase-multilingual-L12-v2 | FP32 | 1867.86 ms | 563.96 / 593.46 ms | 40.44 篇/s | 198.84 ms | 2.79 ms | 6.00 / 9.67 ms | 8.89 / 11.31 ms | 17.10 / 22.19 ms |

> 写入为 24 篇单条 `INSERT`（`ON DUPLICATE KEY UPDATE`），未做批量合并；
> 端到端 = 查询向量化 + seekdb 检索。**FP16 在 onnxruntime-node 的 CPU 路径反而比 FP32 慢**
> （12 层模型无高效 fp16 CPU kernel），浏览器 WASM 后端同理——因此**默认选择 INT8**。

### 5.2 HTTP 全链路（经由运行中的后端 `http://127.0.0.1:5173`）

| 阶段 | 实测 |
| --- | --- |
| `POST /api/import` | 服务端写入 **194.75 ms** + 索引刷新 **2.97 ms**；含向量化与网络的总耗时 **214.14 ms**（24 篇） |
| `POST /api/search` | 服务端检索（纯 seekdb）**中位数 9.59 ms**；端到端（向量传输 + HTTP + 检索）**中位数 26.86 ms**（n=54） |

### 5.3 浏览器内分阶段计时（Chrome 155 headless，WASM 后端）

由 `scripts/browser-bench.js` 驱动真实浏览器、**重复多轮取中位数**（导入 3 轮、查询 5 条），
原始输出见 `data/browser-bench-report.txt`：

| 阶段 | 首次 | 中位数 | 备注 |
| --- | --- | --- | --- |
| ① 模型加载（冷，含从本机 `/models/` 读 21.91 MB INT8 权重） | 834.5 ms | — | 另一次端到端验证中为 746.4 ms；多次运行 669–943 ms |
| ② 文档向量化（24 篇） | 2651.80 ms | **2734.40 ms** | 吞吐 **8.78 篇/秒** |
| ③ 写入 seekdb | 191.70 ms | **114.20 ms** | 24 条 `INSERT`（`ON DUPLICATE KEY UPDATE`） |
| 　索引刷新 | 3.10 ms | **3.10 ms** | |
| ④ 查询向量化 | 14.00 ms | **17.00 ms** | n=5 |
| ⑤ seekdb 检索 | 13.50 ms | **11.90 ms** | n=5 |
| ⑥ **端到端（提交查询 → 结果可展示）** | 97.80 ms | **44.90 ms** | 界面内计时（`performance.now()`） |
| 　端到端（脚本墙钟，含浏览器交互开销） | — | **71.00 ms** | Playwright 观测值，比界面计时更接近用户感知 |

> 浏览器 WASM 比 Node（onnxruntime-node）慢约 10×，符合预期：这是「本地推理」的成本，
> 也是选择 INT8 量化（体积 1/3.93、CPU 推理更快）的直接收益。
> 冷启动时端到端 ~98–117 ms，预热后 ~45 ms；差异来自 ORT 会话与连接池复用。

### 5.4 索引使用证据（EXPLAIN，原生混合检索 SQL）

```
|ID|OPERATOR                     |NAME                                 |EST.ROWS|
|2 |  ├─SUBPLAN SCAN             |_fts                                 |19      |
|5 |  │     └─TEXT RETRIEVAL SCAN|stepfun_docs(ft_stepfun_docs_body)   |19      |   ← 全文索引
|6 |  └─SUBPLAN SCAN             |_vs                                  |24      |
|10|          └─VECTOR INDEX SCAN|stepfun_docs(idx_stepfun_docs_emb)    |24      |   ← HNSW 向量索引
```

### 5.5 检索质量

**评测方法**：数据集为自建双语技术语料 `data/corpus.json`（24 篇文档 + 9 条查询），
每条查询带**人工分级相关性**（0/1/2/3 级）。查询向量与文档向量用同一模型同一量化档位生成，
在 seekdb 中用 §3.3 的混合检索 SQL 取 Top-5。
Recall@5 = (Top-5 命中的分级相关性之和) / (全部相关性之和)；nDCG@5 用增益 `2^g-1`；
MRR 取首个相关结果倒排名均值。**样本量小（9 条查询 / 24 篇文档），仅供横向对比，不作绝对指标。**

| 模型 | 量化 | Recall@5 | nDCG@5 | MRR |
| --- | --- | --- | --- | --- |
| all-MiniLM-L6-v2 | **INT8** | **0.540** | **0.535** | 0.583 |
| all-MiniLM-L6-v2 | FP16 | 0.503 | 0.506 | 0.578 |
| all-MiniLM-L6-v2 | FP32 | 0.503 | 0.506 | 0.578 |
| paraphrase-multilingual-MiniLM-L12-v2 | **INT8** | **0.790** | **0.821** | 0.926 |
| paraphrase-multilingual-MiniLM-L12-v2 | FP32 | 0.790 | 0.829 | 0.944 |

**量化带来的质量变化（结论）**
- **INT8 相对 FP32 质量不降反微升**（all-MiniLM：Recall@5 0.503 → 0.540；multilingual：0.790 → 0.790，
  nDCG 0.829 → 0.821，MRR 0.944 → 0.926）。差异在 9 条查询的小样本上属于噪声量级，
  **结论是：INT8 量化对本任务（384 维句向量检索）的质量影响在测量噪声内，可放心使用**；
  同时体积降到 25.4%、浏览器端吞吐提升约 2×（133.9 vs 66.5 篇/秒）。
- **模型语种覆盖比量化更决定质量**：默认的 `all-MiniLM-L6-v2` 以英文为主，
  中文语义查询召回偏弱；换用多语言模型后 Recall@5 从 0.540 提升到 0.790（+46%），
  代价是 INT8 权重 21.91 MB → 112.83 MB、加载 177 ms → 993 ms。
  **界面「模型」下拉可一键切换，切换后需重新导入文档**（不同模型的向量空间不通用）。

> **交叉验证与测量方差**：`scripts/selftest.js`（独立进程、独立连接、重新灌入语料）
> 测得 all-MiniLM/INT8 的 Recall@5 = 0.5402，与 `scripts/bench.js` 的 0.540 完全一致；
> nDCG@5 0.5241 vs 0.535、MRR 0.5648 vs 0.583 有约 0.02 的差异，
> 来源是原生混合检索在 `_score` 相同时按 `__pk_increment`（插入顺序）做 tie-break，
> 两次运行重新灌入数据后的插入序不同所致 —— 属于该评测口径下的固有波动。

### 5.6 分支消融（why 需要混合检索）

同一批查询、同一份向量，只用单路 vs 混合的 Recall@5（all-MiniLM-L6-v2 / INT8 / 24 篇语料 / 9 条查询）：

| 检索方式 | Recall@5 | 说明 |
| --- | --- | --- |
| 仅向量（`ORDER BY cosine_distance(...) APPROXIMATE`） | 0.452 | 语义召回强，但专有名词/罕见词不敏感 |
| 仅全文（`MATCH(body) AGAINST(...)`） | 0.666 | 关键词精确，但语义同义表达召回不到 |
| **混合（seekdb 内 RRF 融合）** | **0.540** | 两路互补；但由于向量分支在中文上偏弱，混合结果**低于纯全文** |

> 这组数字正是「模型选型比融合权重更关键」的实证：把向量换成多语言模型后，
> 混合检索的 Recall@5 从 0.540 升到 0.790。消融脚本见 `scripts/probes/diag-quality.js`。

---

## 6. 未能验证的部分

| 项 | 原因 | 影响 |
| --- | --- | --- |
| **WebGPU 真实加速** | 本机为无独显服务器，Chrome 155 的 `navigator.gpu.requestAdapter()` 返回 `null`，无法取得 GPU 适配器 | WebGPU **代码路径**（探测 → 候选优先级 → 失败重置 → 徽章提示）已实现并在浏览器内跑通「探测失败 → 降级」分支；但「WebGPU 加速后的耗时数字」**没有实测数据**，§5.3 全部为 WASM 数字 |
| **INT8 / FP16 在 WebGPU 上的权重兼容性** | 同上，无法创建 WebGPU 会话 | 代码里已内置 `webgpu+请求档位 → webgpu+fp16 → webgpu+fp32` 的候选回退，实际生效档位会在界面显示 |
| **百万级语料下的召回/延迟** | 只用了 24 篇语料 | §5.5 的质量数字是小样本结果；HNSW 索引的规模效应未测 |
| **写入批量化** | 当前是逐条 `INSERT`（24 篇 182 ms，约 7.6 ms/篇） | 大批量导入时可合并多值 `INSERT` 进一步优化，**本项目未做** |
| **多用户并发** | 单机单会话验证 | `UNIQUE KEY` + 无用户主键的设计在并发写入下需另行压测 |

---

## 7. 优化点小结

1. **量化**：INT8 量化把 ONNX 权重压到 25.4%（3.93×），浏览器端吞吐约 2×，质量损失在测量噪声内 → 默认档位。
2. **降级**：先用 `requestAdapter()` 判定 WebGPU 可用性，避免失败的 WebGPU 初始化污染 ORT 全局状态；
   必要时用 cache-buster 重新 import 复位 → 降级路径可靠（这是实测发现并修复的真实缺陷）。
3. **索引**：向量侧加 `APPROXIMATE` 才走 HNSW；全文侧改单列 `ik` 分词索引以兼容原生混合检索；
   表去掉用户主键改用 `UNIQUE KEY` → 两条索引同时命中（EXPLAIN 可见）。
4. **融合**：RRF `1/(rank+60)` 在数据库内完成，客户端零拼合 → 满足「混合检索必须由 seekdb 执行」。
5. **离线**：`models/` 本地目录 + `/vendor/ort/` 本地 wasm + `--legacy-peer-deps` 依赖方案 → 全链路无外网依赖。
