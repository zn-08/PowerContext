# 一页技术说明（600077）

## 1. 模块结构

```
minimax/
├── minimax_ctx/              上下文模块
│   ├── config.py             环境变量配置（seekdb / PowerContext / MiniMax）
│   ├── materials.py          seekdb 存储层：建表、全文索引、写入、检索
│   ├── importer.py           导入器：把清单里的文件切成可检索的单元
│   ├── context.py            上下文组装：规格 → 扩展查询 → 代码 / 测试 → 重排
│   ├── powercontext_state.py 状态层：PowerContext Memory 的保存与读回
│   ├── minimax_client.py     MiniMax API 调用与记录（可选）
│   ├── ask.py                把检索到的上下文组装成 prompt，调用 MiniMax 并留痕
│   └── cli.py                命令行入口
├── taskboard/                业务工程（任务看板）
├── specs/                    SDD 规格
├── tests/                    目标测试 + 回归测试
├── demo/run_demo.py          验证样例
└── docs/                     开发记录、技术说明、日志、差异、证据
```

数据流：

```
工程资料 ──import──▶ seekdb.material（FULLTEXT / IK 分词）
                          │
      任务描述 ──context─┴─▶ 规格条目 ──扩展查询──▶ 代码符号 + 测试函数
                          │                             │
                          └──────── ask ───────────────┘
                                    │
                         MiniMax Chat Completion API
                                    │
                     请求 / 回答 / 用量 / 上下文指纹（JSON Lines）

任务状态 ──save-state──▶ PowerContext Memory ──load-state──▶ 任务状态
```

## 2. 上下文怎么选

1. **切分**：Markdown 规格按标题切成条目，Python 源码按顶层符号切分，测试文件按
   测试函数切分。因此一次检索命中的是「条目」，而不是整篇文件。
2. **检索**：先用任务描述检索规格条目；seekdb 的全文索引（`WITH PARSER ik`）负责
   中文与英文的匹配与相关度排序。
3. **扩展**：从命中的规格条目里抽出它点名的标识符、文件名与命令行选项，
   用这些词扩展查询，再去检索代码与测试。这一步解决了「任务用中文描述、
   代码用英文命名」带来的漏检。
4. **重排**：按「规格是否点名了该文件/符号」重排，把真正要改的符号放到最前。
5. **可核对**：每条结果都带仓库相对路径、符号名、相关度与内容摘要，
   来源必须真实存在。

## 3. 技术分工

| 技术 | 角色 | 证据 |
| --- | --- | --- |
| seekdb | 存工程资料与全文索引，检索结果直接构成任务上下文 | `minimax_ctx/materials.py` 的建表 / `ALTER TABLE ... WITH PARSER ik` / `MATCH ... AGAINST`；每次请求打印实际 SQL |
| PowerContext | 存任务进展与验证结果，跨进程读回 | `minimax_ctx/powercontext_state.py` 调 `/v1/memory/remember`、`/v1/memory/search`；演示中由新进程读回 |
| MiniMax | 参与本次代码修改 | `docs/dev-session.md`：任务、提供的上下文、修改过程、代码差异、验证结果；`docs/evidence/minimax-api-log.jsonl`：一次真实 API 调用的请求、回答与用量 |

## 4. 本次修改

| 项 | 内容 |
| --- | --- |
| 任务 | 为任务看板增加按标签筛选任务的能力 |
| 要改什么 | `taskboard.filters.filter_tasks` 增加 `labels`；`taskboard.cli` 增加可重复的 `--label` |
| 必须保持 | 身份唯一、标签归一化、非空校验、仅按状态/负责人筛选时的旧行为、`group_by_status` / `summarize`、JSON 格式 |
| 修改前 | `0577b2d8` |
| 修改后 | `3236fd28` |
| 差异 | `docs/diffs/feature.diff` |
| 验证 | 改前 8 失败 / 改后 9 通过 / 回归 19 通过（日志在 `docs/test-logs/`） |

## 5. 已知限制

- PowerContext 未配置 embedding 模型时，Memory 检索只能用关键词模式（`mode=fts`）。
- scope id 由服务端分配，模块按标题找回自己创建的 scope，重启后仍是同一个。
- 上下文检索是关键词方案，不做语义召回；扩展词来自规格条目，规格越具体越准。
- `ask` 的 API 路径需要 `MINIMAX_API_KEY`；未配置密钥时模块不调用模型，其余流程照常可用。
- 已生成的调用记录里不含任何凭据：密钥只经环境变量注入，用于给请求签名。
