# SPEC-CONTEXT-MODULE：面向 SDD 的上下文模块

- 规格编号：SPEC-CONTEXT-MODULE
- 状态：已接受（2026-11-04）
- 适用模块：`minimax/minimax_ctx`（上下文模块）

## 1. 目标

让一个 AI 开发助手在开始修改代码之前，能够拿到三样东西，并且能证明这三样东西
来自真实存在的文件：

1. 本次任务适用的规格条目；
2. 需要修改的代码文件或符号；
3. 用于验证功能与兼容性的测试。

任务状态（已完成、待办、已有验证结果）必须跨进程保存，重启模块后仍能读回并继续使用。

## 2. 技术分工

| 技术 | 承担的作用 | 接入方式 |
| --- | --- | --- |
| seekdb | 存储工程资料（规格、代码、测试）与全文索引；检索结果用于构造任务上下文 | MySQL 协议（PyMySQL）直连 seekdb 服务端 |
| PowerContext | 保存任务进展与验证结果，并在后续运行中读取、使用 | 官方 Python SDK `powercontext.client` → PowerContext Server 的 HTTP API |
| MiniMax | 参与本次任务的代码生成或修改 | MiniMax Chat Completion API（可选）+ 本次开发会话记录 |

## 3. 规格条目

| 编号 | 条目 |
| --- | --- |
| M-1 | `import` 把清单 `minimax/materials.manifest.json` 声明的资料导入 seekdb 的 `material` 表，并建立全文索引（IK 分词）。 |
| M-2 | 资料按 `spec` / `doc` / `code` / `test` 四类存放，每条记录保留仓库相对路径、符号名与内容 SHA-256。 |
| M-3 | Markdown 规格按标题切分为条目；Python 源码按顶层符号切分；测试文件按测试函数切分。检索因此能定位到“条目”而不是整篇文件。 |
| M-4 | `context` 依据任务描述检索，分别产出规格条目、代码与测试三组结果，每条结果都带真实来源路径与相关度。 |
| M-5 | `save-state` 通过 PowerContext Memory 保存任务状态；`load-state` 能读回同一任务的状态。 |
| M-6 | 从空状态开始保存后，重启模块进程仍能读到该状态，并可与上下文包合并输出（第 4 节“已完成、待办、已有验证结果”）。 |
| M-7 | 规格更新并重新 `import` 后，`context` 的输出必须使用更新后的内容，不得返回固定答案。 |
| M-8 | 模块自身不保存凭据：seekdb 口令、MiniMax 密钥、PowerContext 令牌一律来自环境变量或占位值。 |

## 4. 必须保持的行为

| 编号 | 原有行为 |
| --- | --- |
| P-1 | 业务工程（PowerContext）原有行为不变：本次修改之外不改变任何既有公开接口语义。 |
| P-2 | `competition/` 目录下的判题资产不被修改。 |
| P-3 | 上下文包中的每条来源都可在仓库中打开并核对。 |
| P-4 | 模块在 seekdb 或 PowerContext 不可用时必须明确报错，而不是静默返回空结果。 |
