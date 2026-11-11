# Qoder 开发记录与后续动作

> 这份文件是给**参赛者本人**的执行清单：题面明确要求「项目的主要开发工作必须在 Qoder 中完成」
> 并提交「Qoder 开发记录」，所以下面每一步都要在 Qoder（桌面端 / IDE / JetBrains 插件 / CLI 均可）里操作，
> 并把对话截图或录屏存到 `qoder/evidence/qoder-dev/`。

---

## 一、当前状态（已完成 / 待 Qoder 完成）

| 部分 | 状态 | 说明 |
| --- | --- | --- |
| 项目骨架与核心链路 | ✅ 已完成并跑通 | seekdb 建表、段落导入、全文+向量混合检索、检索足迹、复习计划、前端页面 |
| 端到端自测与证据 | ✅ 已完成 | `qoder/evidence/selftest.md`（真实运行记录）+ `qoder/evidence/screenshots/`（界面截图） |
| 静态展示站 | ✅ 已完成 | `qoder/site/`，含真实运行快照 `snapshot.json` |
| **在 Qoder 里完成主要开发工作** | ⬜ **待完成** | 见第二节的任务清单 |
| **用 Qoder Sites 发布网站** | ⬜ **待完成** | 见第三节 |
| **提测平台提交** | ⬜ 待完成 | 见第四节 |

已完成部分的验证方式（评审可直接复现）：

```bash
cd qoder
npm install && npm run setup-db && npm run selftest
```

## 二、给 Qoder 的开发任务清单

> 建议按顺序做，每个任务结束都**截一张图**（Qoder 对话 + 改动 diff），这些就是开发记录。
> 下面每一段「交给 Qoder 的话」都可以**直接复制粘贴**给 Qoder。

### 任务 1 · 把向量方案升级为可插拔的 provider（推荐第一个做）

**交给 Qoder 的话：**

```
阅读 qoder/src/embed.js 与 qoder/src/db.js。现在 embed() 用的是本地特征哈希 TF-IDF 向量。
请把它重构为可插拔的 provider：

1. 新建 qoder/src/embedders/local.js（把现有特征哈希实现搬过去，行为保持不变）
2. 新建 qoder/src/embedders/openai.js：调用 OpenAI 兼容的 /v1/embeddings 接口
   （配置 EMBED_PROVIDER=openai、EMBED_API_KEY、EMBED_BASE_URL、EMBED_MODEL）
3. qoder/src/embed.js 改成按环境变量选择 provider，默认仍是 local
4. 两种 provider 都要归一化到 L2，维度由 EMBED_DIM 决定；维度不匹配时启动即报错并给出清晰提示
5. 保持现有导出名 embed(text, idf, dim) 与 toVectorLiteral(vector) 不变，调用方不用改
6. 在 qoder/README.md 的「向量方案」一节补一小段，说明两种 provider 的取舍
完成后运行 npm run selftest，确保默认（local）路径的输出与之前一致。
```

**验收标准**：`npm run selftest` 仍然全绿；`EMBED_PROVIDER=openai` 时能清晰提示缺配置项。

### 任务 2 · 补单元测试

**交给 Qoder 的话：**

```
给 qoder 项目补单元测试，用 Node 内置的 node:test runner，不要引入新的测试框架：

1. qoder/test/chunk.test.js：覆盖 chunkText 的段落切分、超长段落按句切、overlap 生效、
   chunkId 对同样输入稳定
2. qoder/test/embed.test.js：覆盖 tokenize 对中文二元组与英文词的切分、embed 输出 L2 范数为 1、
   停用词被过滤、toVectorLiteral 的格式
3. qoder/test/review.test.js：覆盖 SM-2 的打分逻辑（quality<3 重置、quality>=3 间隔递增、
   ease 下限 1.3）
4. 在 package.json 里加 "test": "node --test test/"
5. 运行 npm test，把输出贴进 qoder/evidence/selftest.md 的附录
```

**验收标准**：`npm test` 全绿，测试数量 ≥ 12 条。

### 任务 3 · 优化检索体验

**交给 Qoder 的话：**

```
优化 qoder/public/ 下的检索体验：

1. 检索结果片段里把命中的关键词高亮（前端按 token 命中位置加 <mark>），
   高亮规则要和后端的分词规则一致——请把后端 tokenize 抽成可被前端复用的纯函数
2. 搜索框加「最近检索」下拉，数据来自 seekdb 的 paper_study 表
   （新增一个 GET /api/history?user= 接口，只返回该用户最近 10 条去重后的提问）
3. 结果卡片上增加「排进复习计划」按钮，对接已有的 POST /api/review/schedule
4. 快捷键：/ 聚焦搜索框，Esc 清空
5. 改完后更新 qoder/evidence/screenshots/ 里的截图，并跑一次 npm run selftest
```

**验收标准**：前端无控制台报错；新接口返回真实数据；截图更新。

### 任务 4 · 完善展示站（为 Qoder Sites 发布做准备）

**交给 Qoder 的话：**

```
完善 qoder/site/ 展示页，用于发布到 Qoder Sites：

1. 增加「架构」一节，用 HTML/CSS 画出数据流：浏览器 -> Node 服务 -> seekdb
   （FULLTEXT ik + VECTOR HNSW -> DBMS_HYBRID_SEARCH -> RRF 融合）
2. 增加「使用效果」一节，先放占位说明，并写清楚真实用户数据应该怎么采集
   （站点「访问统计」的访问次数/独立访客，使用日志截图，问卷导出）
3. 把 qoder/evidence/screenshots/ 里的应用截图放到页面里
4. 页面里补上「Qoder 开发记录」一节的占位（说明截图放在仓库 qoder/evidence/qoder-dev/）
5. 保持纯静态（只有 index.html / style.css / site.js / snapshot.json / screenshots/），
   不要引入构建工具
```

**验收标准**：`node scripts/serve-site.js 5175` 能打开，页面无外链依赖。

### 任务 5（加分项）· 接入 PowerContext 作为可选记忆后端

**交给 Qoder 的话：**

```
现在记忆全部存在 seekdb 的 paper_study / paper_review 表里。
请把「复习进度记忆」抽出一个 memory adapter 层：

1. qoder/src/memory/seekdb.js：把现在的实现挪进去
2. qoder/src/memory/powercontext.js：通过 PowerContext 的 HTTP API 读写同一个用户的复习进度
   （配置 POWERCONTEXT_URL、POWERCONTEXT_TOKEN；未配置时不可用）
3. qoder/src/memory/index.js：按环境变量选择后端，未配置 PowerContext 时用 seekdb
4. 两个后端都要有「读 / 写 / 列出待复习」三个能力，接口一致
5. 在 README 里写清楚两者分工：seekdb 负责检索与文档存储，PowerContext 负责跨会话的复习进度记忆
```

**验收标准**：不配置 PowerContext 时行为与现在完全一致；配置后 `npm run selftest` 仍可通过。

## 三、用 Qoder Sites 发布

1. 在 Qoder 里打开本项目（或先克隆仓库）：
   `git clone https://github.com/zn-08/PowerContext.git && cd PowerContext/qoder`
2. 先本地跑一次 `npm run build-site`，确认 `site/` 下有 `index.html`、`style.css`、
   `site.js`、`snapshot.json`、`screenshots/`。
3. 在 Qoder 中使用 **Sites** 功能，新建站点，选择 `qoder/site` 作为站点目录（或把该目录内容上传为站点）。
4. 发布后把可见范围设置为 **「任何人都可访问」**（企业版账号无法设置公开访问，必须用个人账号）。
5. 用浏览器**无痕窗口**打开站点 URL，确认未登录也能正常打开、演示区能切换问法。
6. 把站点完整 URL 记下来，填进提测表单的「项目网站网址」。
7. 题面提醒：**评审结束前不要暂停或删除站点**；发布后修改内容需要再次点「发布」才会更新线上版本。

## 四、提测表单要填什么

入口：https://open.oceanbase.com/train?questionId=600078 （Qoder 赛题）

> 提测表单（2026-11-11 实测）只有 4 项：**代码仓库地址（只读，取报名信息）**、
> **Commit id**、**Branch**、**作品链接**。表单里**没有** UID 输入框，
> 所以 UID 与其余材料要放进仓库：`qoder/evidence/qoder-dev/QODER-UID.txt` 写 UID，
> 开发记录截图放 `qoder/evidence/qoder-dev/`，运行说明与验证样例分别在
> `qoder/README.md` 与 `qoder/evidence/selftest.md`。

| 表单项 | 填什么 |
| --- | --- |
| 作品链接 | Qoder Sites 发布后的完整 URL（可见范围「任何人都可访问」） |
| 代码仓库地址 | 只读，自动取报名信息 `https://github.com/zn-08/PowerContext` |
| 分支 | `2026-competition-ecosystem` |
| Commit id | 提交时的**完整 40 位 SHA**（见下） |
| Qoder 账号 UID（不在表单里） | 写进 `qoder/evidence/qoder-dev/QODER-UID.txt` 并提交到同一 commit |
| 其余材料 | 运行说明、验证样例、Qoder 开发记录（截图/录屏）一并放在 `qoder/` 目录 |

取当前 commit SHA：

```bash
git -C PowerContext rev-parse 2026-competition-ecosystem
```

> 提交前请确认 `qoder/` 目录下的代码、README、QODER-DEV.md、evidence 都在这个 commit 里。

## 五、提交前自检清单

- [ ] `cd qoder && npm install && npm run setup-db && npm run selftest` 全绿
- [ ] `npm start` 后 `http://127.0.0.1:5174` 能检索、能导入、能复习
- [ ] `node scripts/serve-site.js 5175` 站点能打开，演示区能切换问法
- [ ] 无痕窗口能打开 Qoder Sites 站点 URL
- [ ] `evidence/qoder-dev/` 里有需求拆解、编码、调试、发布四个环节的 Qoder 对话截图或录屏
- [ ] `evidence/selftest.md` 与 `evidence/screenshots/` 是本次运行的真实产物
- [ ] README 的运行说明里没有真实 API Key / 密码（示例一律用占位值）
- [ ] 已把 `2026-competition-ecosystem` 分支推送到 GitHub，并取到完整 40 位 commit SHA
