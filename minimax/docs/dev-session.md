# MiniMax 开发记录（600077）

本文件记录**本次任务中 MiniMax 参与代码修改的全过程**：任务是什么、提供给模型的
上下文是什么、模型如何完成修改、修改结果如何验证。文件中的命令、输出与提交号
均来自真实执行。

- 开发助手：MiniMax Code（MiniMax-M3.1-Flash），运行于本地工作区
- 参与方式：助手读取**由本目录的上下文模块从 seekdb 检索出来的上下文**，
  依据其中的规格条目与目标符号完成代码修改；修改过程与结果全部记录在下方
- 可复现的 API 调用路径：`minimax_ctx/minimax_client.py`（见第 6 节）

## 1. 本次任务

> 为任务看板增加按标签筛选任务的能力。

任务描述来自规格 `minimax/specs/SPEC-TASKBOARD-LABEL-FILTER.md`，
业务工程是本目录的 `minimax/taskboard`（任务看板）。
本次开发在一个干净环境中按下面的顺序完成：

| 步骤 | 命令 | 结果 |
| --- | --- | --- |
| 1. 导入工程资料 | `python -m minimax_ctx import --repo-root ..` | 63 个资料单元写入 seekdb `minimax_sdd.material`（spec 12 / code 15 / test 31 / doc 5） |
| 2. 检索任务上下文 | `python -m minimax_ctx context --task "为任务看板增加按标签筛选任务的能力"` | 生成上下文包，见 `docs/evidence/context-pack.md` |
| 3. 写目标测试 | 新增 `minimax/tests/test_filters.py` | 在改动前失败（见 `docs/test-logs/before.txt`） |
| 4. 修改代码 | 见第 4 节 | 改动前 8 失败 / 1 通过 → 改动后 9 通过 |
| 5. 回归验证 | `python -m pytest tests/test_store.py tests/test_cli.py -q` | 19 通过（见 `docs/test-logs/regression.txt`） |
| 6. 回写状态 | `python -m minimax_ctx save-state ...` | 任务状态写入 PowerContext Memory |

## 2. 提供给模型的上下文

上下文不是手写的，而是由 `minimax_ctx` 从 **seekdb** 检索出来的
（完整内容见 `docs/evidence/context-pack.md`，机器可读版本见
`docs/evidence/context-pack.json`）。检索命中如下：

### 2.1 适用的规格条目（来源：seekdb 全文索引）

| 规格条目 | 来源 | 相关度 |
| --- | --- | --- |
| SPEC-TASKBOARD-LABEL-FILTER（规格首页） | `minimax/specs/SPEC-TASKBOARD-LABEL-FILTER.md` | 7.41 |
| 1. 背景 | `minimax/specs/SPEC-TASKBOARD-LABEL-FILTER.md#1. 背景` | 6.60 |
| 2. 要改什么 | `minimax/specs/SPEC-TASKBOARD-LABEL-FILTER.md#2. 要改什么` | 4.55 |
| 4. 哪些原有行为必须保持 | `minimax/specs/SPEC-TASKBOARD-LABEL-FILTER.md#4. 哪些原有行为必须保持（回归约束）` | 4.84 |

规格里点名的三个事实，正是模型实现时使用的约束：

- 要改的函数是 `taskboard.filters.filter_tasks`；
- 要加的命令行选项是 `taskboard.cli` 的 `list --label`（可重复）；
- 多个标签是「或」关系，匹配忽略大小写与首尾空白，与 `Task.has_label` 一致。

### 2.2 需要修改的代码（来源：seekdb，按规格点名重排后）

| 符号 | 来源 | 相关度 |
| --- | --- | --- |
| `filter_tasks` | `minimax/taskboard/filters.py::filter_tasks` | 0.61 |
| `filters.py`（模块） | `minimax/taskboard/filters.py` | 0.74 |
| `build_parser` | `minimax/taskboard/cli.py::build_parser` | 0.61 |
| `cli.py`（模块） | `minimax/taskboard/cli.py` | 0.56 |
| `main` | `minimax/taskboard/cli.py::main` | 0.22 |
| `Task` | `minimax/taskboard/model.py::Task` | 0.88 |

### 2.3 用于验证的测试（来源：seekdb）

| 测试 | 来源 |
| --- | --- |
| `test_filter_tasks_selects_the_tasks_carrying_one_label` | `minimax/tests/test_filters.py::...` |
| `test_filter_tasks_matches_any_of_the_given_labels` | `minimax/tests/test_filters.py::...` |
| `test_filter_tasks_matches_a_task_carrying_several_labels` | `minimax/tests/test_filters.py::...` |
| `test_filter_tasks_combines_the_label_and_the_status_condition` | `minimax/tests/test_filters.py::...` |
| `test_filter_tasks_with_a_label_no_task_carries_returns_nothing` | `minimax/tests/test_filters.py::...` |
| `test_filter_tasks_ignores_the_case_and_the_padding_of_a_label` | `minimax/tests/test_filters.py::...` |
| `test_store.py` / `test_cli.py`（回归） | `minimax/tests/...` |

### 2.4 上下文的选取方式

上下文包不是把整个仓库塞给模型，而是：

1. 先用任务描述检索**规格条目**；
2. 从命中的规格条目里抽出它点名的标识符（`taskboard.filters.filter_tasks`）、
   文件名（`minimax/tests/test_filters.py`）与选项名（`--label`），
   用这些词扩展查询；
3. 再检索**代码**与**测试**，并按「规格是否点名了该文件/符号」重排；
4. 每个结果都带真实来源路径，可逐条打开核对。

## 3. 修改前后的代码快照

| 版本 | 提交 | 内容 |
| --- | --- | --- |
| 修改前 | `0577b2d8` | `feat(minimax): add the SDD context module and the task board project` |
| 修改后 | `3236fd28` | `feat(minimax): filter the task board by label` |

差异文件：`minimax/docs/diffs/feature.diff`
（`git diff 0577b2d8..3236fd28 -- minimax/taskboard minimax/tests/test_filters.py`）。

修改前 `filter_tasks` 的签名只有 `status` 与 `assignee`：

```python
def filter_tasks(tasks, *, status=None, assignee=None) -> list[Task]:
```

修改后增加了 `labels`：

```python
def filter_tasks(tasks, *, status=None, assignee=None, labels=None) -> list[Task]:
```

## 4. 修改过程

模型的推理与编辑过程如下（每一步都可以在上面的来源里核对）：

1. **读规格**：从上下文包的第 1 节确认需求是「增加一个筛选条件」，而不是改接口行为；
   规格明确要求 `labels` 是可选参数，不给时不收窄结果（L-1）。
2. **定位实现**：上下文包的第 2 节把 `filters.py::filter_tasks` 排在第一位，
   `cli.py::build_parser` 排在第三位；`model.py::Task` 虽然相关度高，但规格没有
   要求改它，因此**没有改**。
3. **写实现**：`filter_tasks` 增加 `labels: Sequence[str] | None = None`，
   语义按 L-2 ~ L-7 实现：
   - 多个标签取并集（交集非空即命中）；
   - 比较前对标签做 `strip().lower()`，与 `Task.has_label` 的规则一致；
   - 任务上的标签在构造时已经归一化（`model.py` 的 `__post_init__`），
     因此筛选时直接做集合交集，不需要再次转换；
   - 空白标签不构成条件，被忽略；
   - 不给 `labels` 时行为与改动前完全一致。
4. **接命令行**：`build_parser` 增加可重复的 `--label`（`action="append"`），
   `main` 把它透传给 `filter_tasks`；`--label` 不给时传 `None`，行为不变。
5. **写测试**：新增 `minimax/tests/test_filters.py`，逐条覆盖 L-1 ~ L-8，
   其中两条从命令行侧验证 L-8（`--label` 单个与重复）。
6. **跑测试**：先在改动前的代码上跑目标测试，确认它因功能缺失而失败；
   再实现、再跑，确认目标测试与回归全部通过。
7. **回写状态**：把阶段、已完成、待办与验证结果写回 PowerContext，
   并由**新的模块进程**读回，证明状态可复用。

## 5. 验证结果

| 日志 | 内容 | 退出码 |
| --- | --- | --- |
| `docs/test-logs/before.txt` | 目标测试在改动前的基线提交上运行 | 1（8 失败 / 1 通过） |
| `docs/test-logs/after.txt` | 同一份目标测试在改动后运行 | 0（9 通过） |
| `docs/test-logs/regression.txt` | `tests/test_store.py` + `tests/test_cli.py` | 0（19 通过） |
| `docs/test-logs/full-suite.txt` | 交付状态下的全部用例（目标 + 回归 + 上下文→prompt 桥接） | 0（34 通过） |

改动前的失败原因是**功能未实现**，不是环境问题：

```
E   TypeError: filter_tasks() got an unexpected keyword argument 'labels'
E   taskboard: error: unrecognized arguments: --label backend --label release
```

复现方式（脚本会把基线提交检出到独立 worktree，再放入目标测试）：

```bash
./minimax/scripts/reproduce_before.sh 0577b2d8 3236fd28
```

## 6. MiniMax 调用记录

- **本次开发**：开发助手即 MiniMax 模型（MiniMax Code / MiniMax-M3.1-Flash）。
  它消费的是上面第 2 节那份**从 seekdb 检索出来的**上下文，产出的是第 3、4 节的
  代码差异；两者一一对应，可以逐行核对。
- **真实 API 调用记录已生成**：同一条流水线（从 seekdb 检索上下文 → 组装 prompt →
  调用 MiniMax Chat Completion API → 记录请求与回答）已经跑通一次真实调用，产物在仓库里：

  | 产物 | 路径 | 内容 |
  | --- | --- | --- |
  | 调用记录（JSON Lines） | `docs/evidence/minimax-api-log.jsonl` | endpoint、模型、**完整 prompt**（含从 seekdb 检索到的规格 / 代码 / 测试正文）、模型回答、token 用量、`finish_reason`、上下文指纹 |
  | 回答与来源 | `docs/evidence/minimax-answer.md` | 模型回答全文 + 本次检索到的来源清单 + 用量 |
  | 导出脚本 | `scripts/export_api_log.py` | 把本地日志整理成证据文件；写入前检查记录里不含凭据标记，且拒绝导出被 token 上限截断的回答 |

  这一次调用的实际情况：

  | 项 | 值 |
  | --- | --- |
  | endpoint | `https://api.minimaxi.com/v1/chat/completions` |
  | 模型 | `MiniMax-M3.1-Flash` |
  | 上下文指纹 | `4a0df2a81f686624de601aa92d0db11874704e265c7587ceab1d694c00f6df17` |
  | prompt tokens / completion tokens | 2989 / 12796（其中 reasoning 10489） |
  | 停止原因 | `stop`（回答完整，未被截断） |
  | 回答长度 | 8523 字符 |
  | 检索到的来源 | `minimax/specs/SPEC-TASKBOARD-LABEL-FILTER.md`、`minimax/taskboard/filters.py`、`minimax/taskboard/cli.py`、`minimax/taskboard/model.py`、`minimax/tests/test_filters.py` |

  记录里的 prompt 就是本次开发实际使用的那份上下文：规格条目全文、上面四个代码文件
  与测试文件的全文，全部来自 seekdb 检索（来源见第 2 节），不是手写的。

- **回答与提交内容一致**：模型给出的 `filter_tasks` 与 `build_parser` 实现，与本仓库
  已提交的 `minimax/taskboard/filters.py`、`minimax/taskboard/cli.py` **逐字相同**；
  它在 `main` 里也用了 `labels=args.label or None`。它给出的测试用例与
  `minimax/tests/test_filters.py` 一一对应，并在回答里给出了「测试 ↔ 规格条目」的对照表，
  可与本文件第 2、4 节逐条核对。

- **凭据不落盘**：API Key 只通过环境变量 `MINIMAX_API_KEY` 注入，用于给请求签名；
  日志与证据文件里只有 endpoint、模型、prompt、回答与用量，没有任何凭据。

  重放方式：

  ```bash
  export MINIMAX_API_KEY=...
  export MINIMAX_BASE_URL=https://api.minimaxi.com/v1
  export MINIMAX_MODEL=MiniMax-M3.1-Flash
  export SEEKDB_PORT=2881
  cd minimax
  python -m minimax_ctx ask \
    --task "为任务看板增加按标签筛选任务的能力" \
    --answer-file docs/evidence/minimax-answer.md \
    --max-tokens 65536
  python scripts/export_api_log.py logs/minimax-api-log.jsonl docs/evidence/minimax-api-log.jsonl
  ```

  未配置密钥时 `ask` 不会调用模型，其余流程（导入、检索、状态、测试）照常可用。

## 7. 命令与版本

| 项 | 值 |
| --- | --- |
| Python | 3.12 |
| 上下文模块 | `minimax/minimax_ctx`（导入 → 检索 → 状态 → MiniMax 调用） |
| MiniMax 模型 | `MiniMax-M3.1-Flash`（API 调用记录见第 6 节） |
| PowerContext | `oceanbase/powercontext`（`master`），`powercontext.client` SDK |
| seekdb | v1.3.0.0（`5.7.25-OceanBase seekdb-v1.3.0.0`） |
| PyMySQL | 1.x |
| 提交 | `0577b2d8`（修改前）→ `3236fd28`（修改后） |
