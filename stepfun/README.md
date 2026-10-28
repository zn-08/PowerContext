# 阶跃星辰：端侧 AI 推理性能优化与 seekdb 混合检索

> 浏览器端迷你知识库：**文档向量与查询向量全部在用户设备上计算**（Transformers.js + ONNX Runtime Web），
> **向量 / 原文 / 元数据全部存放在 seekdb**，**混合检索由 seekdb 内部的一条 SQL 完成**。

---

## 1. 赛题契约对照

| 检查项 | 本项目实现 | 位置 |
| --- | --- | --- |
| 完整流程 | 导入文本 → 浏览器内生成向量 → 写入 seekdb → 输入查询 → 展示排序结果 | `public/app.js` |
| 本地推理 | 文档与查询的 embedding 均在浏览器内计算，**无任何云端推理 API 调用**（服务端不 import transformers） | `public/app.js` `loadModel()` / `embed()` |
| 模型量化 | INT8（默认）/ FP16 / FP32 三档，本地 ONNX 权重，提供来源、量化方式、体积对比与质量变化 | `scripts/fetch-model.js`、`data/bench-report.txt` |
| 后端与降级 | 优先 WebGPU，不可用时自动降级 WASM，界面顶部徽章 + 面板明确显示当前后端与降级原因 | `public/app.js` `probeWebGPU()` / `loadModel()` |
| seekdb 数据层 | 向量、原文、元数据存于 seekdb；混合检索在 seekdb 中完成（`DBMS_HYBRID_SEARCH` 生成的单条 SQL） | `server/db.js` `hybridSearchNative()` |
| 状态与刷新 | 增量导入（`ON DUPLICATE KEY UPDATE`）、索引刷新后新增内容立刻可检索、会话状态与集合状态接口 | `server/db.js` `upsertDocs()` / `refreshIndexes()` |
| 可复现性 | 见下文「离线安装 / 启动 / 操作演示 / 自测」 | `README.md` |

**红线自查**
- ❌ 不调用云端推理 API —— 后端 `server/` 里没有任何 `import ... from '@huggingface/transformers'`，向量只来自浏览器。
- ❌ 不在客户端拼合两路结果 —— 客户端只提交「查询文本 + 查询向量」，**融合与排序全部在 seekdb 内部**（见 `server/db.js` 中执行的单条 SQL）。
- ❌ 不用客户端内存检索替代数据库 —— 候选集由 seekdb 的 `FULLTEXT` 索引与 `VECTOR(HNSW)` 索引裁剪，EXPLAIN 可见（见 `TECHNICAL-NOTE.md`）。

---

## 2. 环境要求

| 组件 | 版本（实测环境） | 说明 |
| --- | --- | --- |
| Node.js | v24.20.0（需 ≥ 20） | 后端为原生 `node:http`，无框架依赖 |
| npm | 11.19.0 | **Windows PowerShell 下必须用 `npm.cmd` 或 `cmd /c npm ...`**（执行策略禁 `npm.ps1`） |
| seekdb | `5.7.25-OceanBase seekdb-v1.3.0.0` | 已在本机运行：`127.0.0.1:2881`，用户 `root`，空密码 |
| 浏览器 | Chrome / Edge 155+ | 需支持 WebAssembly；WebGPU 可选（不支持则自动降级 WASM） |
| 操作系统 | Windows 10/11 x64 | 也可在 Linux/macOS 运行 |

> **seekdb 服务端地址可用环境变量覆盖**，无需改代码（见 §6）。

---

## 3. 目录结构

```
stepfun/
├─ README.md                  # 本文件：运行说明
├─ TECHNICAL-NOTE.md          # 一页技术说明（含实测值）
├─ package.json               # 依赖与脚本
├─ server/
│  ├─ index.js                # 极薄 HTTP 后端：静态托管 + API 代理（不计算 embedding）
│  ├─ db.js                   # ★ seekdb 数据访问层 + 混合检索 SQL
│  └─ config.js               # 环境变量配置 + 本地模型清单扫描
├─ public/
│  ├─ index.html              # 界面
│  ├─ app.js                  # ★ 端侧推理 + 降级 + 导入 + 检索
│  └─ style.css
├─ scripts/
│  ├─ setup-db.js             # 只建表建索引（DDL）
│  ├─ selftest.js             # Node 侧等价端到端自测
│  ├─ bench.js                # Node 侧性能与质量基准
│  ├─ browser-test.js         # ★ 真实浏览器端到端验证（Chrome/Edge）
│  ├─ browser-bench.js        # 浏览器内分阶段基准（中位数）
│  ├─ fetch-model.js          # 下载本地模型权重（支持 HTTP 代理）
│  ├─ lib/downloader.js       # 支持代理的极简下载器
│  └─ probes/                 # 探查 seekdb 能力用的一次性脚本（保留作参考）
├─ data/
│  ├─ corpus.json             # 演示语料 + 9 条带人工分级相关性的评测查询
│  ├─ bench-report.txt/.json  # Node 侧基准实测结果
│  ├─ browser-bench-report.txt
│  ├─ browser-test.png        # 浏览器端验证截图
│  └─ browser-test-report.txt
└─ models/                    # 本地模型权重（不入库，见 §5）
   └─ Xenova/
      ├─ all-MiniLM-L6-v2/    # 默认：英文为主，21.91 MB (INT8)
      └─ paraphrase-multilingual-MiniLM-L12-v2/   # 可选：多语言，112.83 MB (INT8)
```

> `node_modules/` 与 `models/` 属于「可再生成的依赖/权重」，**不属于交付内容**，需按 §5 自行获取。

---

## 4. 安装依赖

### 4.1 在线安装

```powershell
cd D:\A_BIT\学习\大三上\Occeanbase高级数据库技术\比赛\work\stage\stepfun
cmd /c npm install --legacy-peer-deps
```

> **为什么需要 `--legacy-peer-deps`**：官方 SDK `seekdb@1.4.0` 声明了 peerDependency `@seekdb/default-embed@^1.2.0`，
> 而后者自身带着 `peerDependencies: { "@seekdb/common": "workspace:*", seekdb: "workspace:*" }`——
> 这是上游 monorepo 发布时把 workspace 协议打进产物导致的。**我们不使用 `@seekdb/default-embed`**
> （向量由浏览器端自己算），因此跳过 peer 解析即可。
> 不加该参数会报 `Unsupported URL Type "workspace:"`。

### 4.2 离线安装

评审环境可能无外网。提前在有网机器上准备依赖：

```powershell
# 有网机器
cmd /c npm install --legacy-peer-deps
cmd /c npm pack            # 或直接整体拷贝 node_modules/
```

然后把 `node_modules/` 目录整体拷贝到目标机器的 `stepfun/` 下即可（不含 devDependencies 时用
`cmd /c npm install --omit=dev --legacy-peer-deps` 更小）。离线环境无需再执行任何安装命令。

依赖清单（`package.json`）：

| 包 | 版本 | 用途 |
| --- | --- | --- |
| `seekdb` | ^1.4.0 | **官方 JS/TS SDK**（Server 模式，底层 mysql2，Windows 可用） |
| `mysql2` | ^3.11.5 | `seekdb` 的底层驱动；另用于 EXPLAIN 诊断与原生混合检索的会话变量 |
| `@huggingface/transformers` | 3.7.6 | 端侧 embedding（浏览器 + Node 侧等价验证） |
| `playwright-core`（devDependency） | ^1.64.1 | 仅用于浏览器自动验证脚本；驱动系统已装的 Chrome/Edge，**不下载额外浏览器** |

---

## 5. 模型放置（离线可用）

模型从 **本机 `models/` 目录** 加载，浏览器通过 `env.localModelPath = '/models/'` 指向它，
**完全不需要访问 huggingface.co**。

### 5.1 目录约定

Transformers.js 解析本地路径的规则是 `<localModelPath>/<model_id>/<file>`，因此：

```
models/
└─ Xenova/
   ├─ all-MiniLM-L6-v2/
   │  ├─ config.json
   │  ├─ tokenizer.json
   │  ├─ tokenizer_config.json
   │  ├─ special_tokens_map.json
   │  └─ onnx/
   │     ├─ model.onnx              # FP32  86.20 MB
   │     ├─ model_fp16.onnx         # FP16  43.20 MB
   │     └─ model_quantized.onnx    # INT8  21.91 MB   ← 默认使用
   └─ paraphrase-multilingual-MiniLM-L12-v2/
      ├─ config.json / tokenizer.json / tokenizer_config.json / special_tokens_map.json
      └─ onnx/
         ├─ model.onnx              # FP32 448.48 MB
         ├─ model_fp16.onnx         # FP16 224.43 MB
         └─ model_quantized.onnx    # INT8 112.83 MB
```

### 5.2 在线下载（脚本自带代理支持）

```powershell
node scripts\fetch-model.js                                   # 默认 all-MiniLM-L6-v2
$env:MODEL_REPO='Xenova/paraphrase-multilingual-MiniLM-L12-v2'; node scripts\fetch-model.js
```

`scripts/lib/downloader.js` 用内置 `http`/`https` 实现了 HTTP CONNECT 隧道，
会读取 `HTTP_PROXY` / `HTTPS_PROXY` 环境变量（Node 24 的 `fetch` 默认**不**读这两个变量）。

### 5.3 离线获取

在有网机器上下载后拷贝 `models/` 目录即可；或直接执行上面的 `fetch-model.js` 后拷贝整个 `models/`。
若评审环境完全无外网，请确认浏览器侧 `env.allowRemoteModels` 为 `false`
（服务端环境变量 `ALLOW_REMOTE_MODELS=0`），此时模型只从本地目录解析。

---

## 6. seekdb 连接配置

全部通过**环境变量**覆盖，无需改代码：

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `SEEKDB_HOST` | `127.0.0.1` | seekdb 地址 |
| `SEEKDB_PORT` | `2881` | seekdb 端口 |
| `SEEKDB_USER` | `root` | 用户名 |
| `SEEKDB_PASSWORD` | *(空)* | 密码 |
| `SEEKDB_DATABASE` | `stepfun_kb` | 数据库名（不存在会自动创建） |
| `SEEKDB_TABLE` | `stepfun_docs` | 集合（表）名 |
| `EMBED_DIM` | `384` | 向量维度，须与模型一致 |
| `PORT` | `5173` | 应用 HTTP 端口 |
| `HOST` | `127.0.0.1` | 应用监听地址 |
| `MODEL_ID` | `Xenova/all-MiniLM-L6-v2` | 默认模型（界面里也能切） |
| `MODEL_DTYPE` | `q8` | 默认量化档位 |
| `ALLOW_REMOTE_MODELS` | `1` | 设为 `0` 禁止浏览器联网下载模型 |

示例（PowerShell）：

```powershell
$env:SEEKDB_HOST='127.0.0.1'; $env:SEEKDB_PORT='2881'; $env:SEEKDB_USER='root'
$env:SEEKDB_PASSWORD=''; $env:SEEKDB_DATABASE='stepfun_kb'; $env:PORT='5173'
```

> **接入方式说明**：`seekdb` 官方 JS SDK 的 **Server 模式**（`new SeekdbClient({ host, port, user, password, database })`）。
> 该包是纯 JS 实现（唯一依赖是 `mysql2`），不依赖 `@oceanbase/seekdb-node-*` 原生运行时，
> 因此 **Windows 上直接可用**；原生运行时包只提供 linux/darwin 预编译产物。

---

## 7. 启动

```powershell
cd D:\A_BIT\学习\大三上\Occeanbase高级数据库技术\比赛\work\stage\stepfun

# ① （可选）先建表建索引；不执行的话启动时会自动建
node scripts\setup-db.js

# ② 启动应用
node server\index.js
```

输出示例：

```
== 连接 seekdb ==
   127.0.0.1:2881  database=stepfun_kb table=stepfun_docs
   schema ready
== 应用已启动 ==  http://127.0.0.1:5173
   模型(浏览器端本地加载): Xenova/all-MiniLM-L6-v2  dtype=q8
   本地模型目录: ...\stepfun\models
```

浏览器打开 <http://127.0.0.1:5173>。

---

## 8. 操作演示

1. **端侧推理面板**：页面加载即自动加载模型。顶部徽章显示当前推理后端
   （`WebGPU` / `WASM`）、量化档位与模型名；左侧面板给出**降级原因**、模型来源、
   **模型体积**与**加载耗时**。可用「量化档位」下拉切换 INT8 / FP16 / FP32，
   用「模型」下拉切换两个本地模型（切换后重新导入文档，因为不同模型的向量空间不同）。
2. **导入文档**
   - 点「载入演示语料」自动填入 24 篇中英文混合的技术文档；或直接在文本框里
     按 `标题 | 正文` 每行一篇粘贴。
   - 点「向量化并写入 seekdb」。**向量化在浏览器内完成**，面板显示
     *向量化耗时 / 吞吐（篇/秒）*、*写入耗时*、*索引刷新耗时*、*集合文档总数*。
3. **检索**
   - 输入查询（面板上有 4 个示例查询按钮），选 Top-K，选混合检索实现方式
     （`原生 DBMS_HYBRID_SEARCH` / `手写单条 RRF SQL`），点「检索」。
   - 面板显示 **查询向量化 / seekdb 检索 / 端到端延迟** 三个分阶段耗时，
     以及命中方式统计（`both` / `vector` / `fulltext`）。
   - 结果列表按 seekdb 返回的融合分数排序，每条显示 `score`、`vec_rank`（向量分支排名）、
     `ft_rank`（全文分支排名）与命中来源标签。
4. **查看混合检索 SQL**：页面底部可展开，由 **seekdb 亲自生成**的那条混合检索 SQL
   （`match(body) against(...)` 全文候选 + `ORDER BY _distance APPROXIMATE` 向量候选
   + RRF 融合），向量字面量已缩写便于阅读。
   点左侧「查看混合检索执行计划」可看到 EXPLAIN 输出，能看到
   `TEXT RETRIEVAL SCAN`（全文索引）与 `VECTOR INDEX SCAN`（HNSW 向量索引）都被用上。
5. **增量导入与刷新**：再次导入新文档 → 集合文档数增加 → 立刻用新文档里的关键词检索，
   新文档即可被检索到（seekdb 的全文索引在事务提交后生效）。
6. **状态**：「seekdb 状态」卡片显示连接状态、版本、库/集合名、文档数与会话 ID。

---

## 9. 自测与基准

```powershell
# Node 侧等价端到端自测（连接 seekdb → embedding → 混合检索 SQL → 增量导入 → 索引刷新 → 质量评测）
node scripts\selftest.js

# 真实浏览器端到端验证（需要先启动 node server\index.js）
node scripts\browser-test.js          # 加 --headed 可看到界面

# 浏览器内分阶段基准（中位数）
node scripts\browser-bench.js

# Node 侧性能 + 质量基准（多模型 × 多量化档位）
node scripts\bench.js
```

实测结果见 `data/bench-report.txt`、`data/browser-bench-report.txt`、`data/browser-test-report.txt`
与 `TECHNICAL-NOTE.md`。

---

## 10. 已知限制

- **WebGPU 未在本机验证**：本机为无独显服务器，Chrome 155 的
  `navigator.gpu.requestAdapter()` 返回 `null`，因此实测均走 **WASM 降级路径**。
  WebGPU 分支的代码路径已完成（探测 → 候选优先级 → 失败重置），
  但「WebGPU 真实加速」这一条**没有实测数据**，详见 `TECHNICAL-NOTE.md` §6。
- **演示语料仅 24 篇**，质量指标（Recall@5 / nDCG@5）只反映该小语料上的表现，
  评测集与分级标准见 `data/corpus.json`。
- 默认模型 `all-MiniLM-L6-v2` 以英文为主，中文语义召回偏弱；
  换成 `paraphrase-multilingual-MiniLM-L12-v2` 后 Recall@5 由 0.540 提升到 0.790（实测），
  代价是 INT8 权重从 21.91 MB 增至 112.83 MB。
