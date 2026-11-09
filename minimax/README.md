# MiniMax × OceanBase：面向软件持续演进的 SDD 上下文管理系统

本目录是第六届 OceanBase 数据库大赛 **MiniMax 赛题（600077）** 的完整交付物。

> 题目：为 AI 开发助手实现上下文模块，提供任务所需的规格、代码和测试，
> 并使用 MiniMax 完成一次实际代码修改。

## 1. 一页技术说明

| 层次 | 组成 | 作用 |
| --- | --- | --- |
| 上下文模块 | `minimax_ctx/` | 导入工程资料、检索任务上下文、保存与复用任务状态 |
| 业务工程 | `taskboard/` | 任务看板：一个可运行、有测试的小型业务工程，是本次修改的对象 |
| 规格 | `specs/` | 两份 SDD 规格：本次修改的规格，以及上下文模块自身的规格 |
| 证据 | `docs/` | 开发记录、测试日志、上下文包、演示报告 |

**技术分工**

| 技术 | 承担的作用 | 接入方式与版本 |
| --- | --- | --- |
| seekdb | 存储工程资料（规格 / 代码 / 测试）并建立全文索引；检索结果直接构成任务上下文 | MySQL 协议，PyMySQL >= 1.0.2，seekdb v1.3.0.0（Linux 评审环境请用 seekdb v1.4.0） |
| PowerContext | 保存任务进展（阶段、已完成、待办、已有验证结果），并在后续运行中读取、使用 | 官方 Python SDK `powercontext.client` → PowerContext Server HTTP API `/v1/memory/*`；Server 使用 SQLite 持久化 |
| MiniMax | 参与本次任务的代码生成与修改 | 本次开发会话（见 `docs/dev-session.md`）；`minimax_ctx/ask.py` 用检索到的上下文发起一次真实调用，记录在 `docs/evidence/minimax-api-log.jsonl` |

**上下文是怎么选出来的**

1. `import` 按 `materials.manifest.json` 把规格、代码、测试导入 seekdb 的 `material` 表，
   建立 `WITH PARSER ik` 的全文索引；Markdown 按标题切成规格条目，Python 按顶层符号切分，
   测试文件按测试函数切分，因此检索命中的是「条目」而不是整篇文件。
2. `context` 先用任务描述检索**规格条目**；再从检索到的规格里抽出它点名的
   模块路径、符号名与命令行选项（`taskboard.filters.filter_tasks`、`--label` …），
   用这些词**扩展查询**后再检索**代码**与**测试**；最后按「规格是否点名了该文件/符号」
   重排，让真正要改的符号排在前面。
3. 上下文包里每一条都带真实来源（仓库相对路径 + 符号名 + 相关度），可以直接打开核对。

## 2. 这次要改什么

**任务**：为任务看板增加按标签筛选任务的能力。

**要改什么**（规格 `specs/SPEC-TASKBOARD-LABEL-FILTER.md` 条目 L-1 ~ L-8）

- `taskboard.filters.filter_tasks` 增加可选参数 `labels`；
- `taskboard.cli` 的 `list` 子命令增加可重复的 `--label` 选项；
- 多个标签之间是「或」；标签匹配忽略大小写与首尾空白，与 `Task.has_label` 一致。

**哪些原有行为必须保持**（同规格条目 K-1 ~ K-6）

- 任务身份唯一、重复身份被拒绝；
- 标签在创建时去空白并转小写，`has_label` 行为不变；
- 标题 / 身份不得为空、估计值不得为负；
- 只给状态或只给负责人时，筛选结果与改动前一致；
- `group_by_status`、`summarize` 与看板 JSON 格式不变。

## 3. 怎么跑

### 3.1 依赖

| 组件 | 版本 |
| --- | --- |
| Python | 3.11+ |
| seekdb | v1.3.0.0 起（含 `WITH PARSER ik` 的全文索引） |
| PowerContext | `oceanbase/powercontext` 的 `master` 或 `2026-competition` 分支 |
| PyMySQL | >= 1.0.2 |
| pytest | >= 8.0 |

```bash
pip install -r minimax/requirements.txt
```

### 3.2 启动两个服务

```bash
# 1) seekdb（工程资料存这里）
SEEKDB_BIN=/opt/seekdb/bin/seekdb ./minimax/scripts/start_seekdb.sh ./seekdb_run 2881
export SEEKDB_PORT=2881

# 2) PowerContext（任务状态存这里）
export PYTHONPATH=/path/to/powercontext/src
./minimax/scripts/start_powercontext.sh 8787 ./powercontext_data
export POWERCONTEXT_URL=http://127.0.0.1:8787
```

### 3.3 跑验证样例

```bash
cd minimax
python demo/run_demo.py
```

样例依次完成：健康检查 → 导入资料 → 生成上下文包 → 保存任务状态 →
**在新进程里读回状态** → 运行目标测试与回归测试 → 把验证结果写回状态。
产物在 `minimax/docs/evidence/`。

### 3.4 单独使用各入口

```bash
cd minimax
python -m minimax_ctx init                      # 建表 + 全文索引
python -m minimax_ctx import --repo-root ..     # 导入工程资料
python -m minimax_ctx context --task "为任务看板增加按标签筛选任务的能力"
python -m minimax_ctx save-state --task "..." --done "..." --todo "..."
python -m minimax_ctx load-state --task "..."
python -m minimax_ctx list-state
python -m minimax_ctx health
python -m minimax_ctx selftest

# 用检索到的上下文问一次 MiniMax（需要 MINIMAX_API_KEY）
python -m minimax_ctx ask --task "为任务看板增加按标签筛选任务的能力" \
    --answer-file docs/evidence/minimax-answer.md
python scripts/export_api_log.py logs/minimax-api-log.jsonl docs/evidence/minimax-api-log.jsonl
```

### 3.5 测试

```bash
cd minimax
python -m pytest tests -q          # 目标测试 + 回归测试
```

### 3.6 复现「改动前失败」

目标测试在改动前并不存在，所以复现脚本把基线提交检出到独立 worktree，
再把目标测试放进去运行 —— 失败原因是功能未实现，而不是环境问题：

```bash
./minimax/scripts/reproduce_before.sh <基线commit> <目标commit>
```

结果见 `docs/test-logs/before.txt`；改动后见 `docs/test-logs/after.txt`，
回归见 `docs/test-logs/regression.txt`。

## 4. 目录

| 路径 | 内容 |
| --- | --- |
| `minimax_ctx/` | 上下文模块（CLI + 存储层 + 状态层 + 上下文组装 + MiniMax 调用） |
| `taskboard/` | 业务工程：任务看板（模型 / 存储 / 筛选 / 命令行） |
| `specs/` | SDD 规格 |
| `tests/` | 目标测试与回归测试 |
| `demo/run_demo.py` | 验证样例 |
| `scripts/` | 启动脚本、调用记录导出脚本与「改动前失败」复现脚本 |
| `docs/dev-session.md` | MiniMax 开发记录：任务、上下文、修改过程、代码差异 |
| `docs/technical-note.md` | 一页技术说明 |
| `docs/test-logs/` | 改动前 / 改动后 / 回归测试日志 |
| `docs/diffs/` | 修改前后的代码差异 |
| `docs/evidence/` | 演示产物：上下文包、测试输出、演示报告、MiniMax 调用记录与回答 |
| `materials.manifest.json` | 导入清单：哪些文件是规格、代码、测试 |

## 5. 验收项对照

| 验收项 | 交付位置 |
| --- | --- |
| 能够运行 | `README.md` 第 3 节 + `demo/run_demo.py`；`scripts/` 提供两个服务的启动脚本 |
| MiniMax 实际参与 | `docs/dev-session.md`：任务、提供给模型的上下文、修改过程、代码差异；`docs/evidence/minimax-api-log.jsonl`：一次真实 API 调用的完整请求与回答；`docs/evidence/minimax-answer.md`：模型回答全文 |
| 上下文正确 | `minimax_ctx/context.py` 的上下文包（带真实来源）；`docs/evidence/context-pack.md`；规格更新后重新 `import` 即生效 |
| 数据库实际参与 | `minimax_ctx/materials.py`：在 seekdb 中建表、建全文索引、写入并检索；每次请求都打印实际 SQL |
| 状态能够复用 | `minimax_ctx/powercontext_state.py`：通过 PowerContext Memory 保存与读取；演示中由**新进程**读回 |
| 代码修改有效 | `tests/test_filters.py`（改前失败 / 改后通过）+ `tests/test_store.py`、`tests/test_cli.py` 回归全绿；日志在 `docs/test-logs/` |

## 6. 已知限制

- PowerContext Server 未配置 embedding 模型时，Memory 检索只能走关键词模式
  （`mode=fts`）；本模块固定使用该模式，避免依赖推理服务。
- PowerContext 的 scope id 由服务端分配，模块按标题 `MiniMax SDD task board`
  找回自己创建的 scope，因此重启后仍是同一个 scope。
- 上下文检索是**关键词 + 全文索引**方案，不做语义召回；扩展查询的词来自规格条目，
  规格写得越具体，检索越准。
- 模块不保存任何凭据：seekdb 口令、MiniMax 密钥、PowerContext 令牌一律来自环境变量。
- `minimax_ctx/ask.py` 的 API 路径需要 `MINIMAX_API_KEY`：未配置密钥时不会调用模型，
  其余流程照常可用；已生成的调用记录里不含任何凭据（`scripts/export_api_log.py`
  在写入证据文件前会检查记录中不含凭据标记）。
