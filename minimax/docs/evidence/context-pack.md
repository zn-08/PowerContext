# 任务上下文包 / Task Context Pack

- 任务 / task: 为任务看板增加按标签筛选任务的能力
- 检索式 / query: 为任务看板增加按标签筛选任务的能力
- 生成时间 / generated_at: 2026-11-04T15:08:36+00:00
- 上下文指纹 / context_digest: f51478490cff2ed2ae5b0b212896485bc9233d4904a608ddcf5409515c963946

## 1. 本次任务适用的规格条目 / applicable specifications

- **SPEC-TASKBOARD-LABEL-FILTER.md::SPEC-TASKBOARD-LABEL-FILTER.md#SPEC-TASKBOARD-LABEL-FILTER：任务看板的标签筛选**
  - 来源 / source: `minimax/specs/SPEC-TASKBOARD-LABEL-FILTER.md`#SPEC-TASKBOARD-LABEL-FILTER.md#SPEC-TASKBOARD-LABEL-FILTER：任务看板的标签筛选
  - 相关度 / relevance: 7.409999686165966
  - 摘要 / summary: - 规格编号：SPEC-TASKBOARD-LABEL-FILTER - 状态：已接受（2026-11-04） - 适用模块：`taskboard.filters`、`taskboard.cli` - 关联任务：为 MiniMax × OceanBase 赛题完成一次真实、可验证的局部修改
- **SPEC-TASKBOARD-LABEL-FILTER.md::SPEC-TASKBOARD-LABEL-FILTER.md#1. 背景**
  - 来源 / source: `minimax/specs/SPEC-TASKBOARD-LABEL-FILTER.md`#SPEC-TASKBOARD-LABEL-FILTER.md#1. 背景
  - 相关度 / relevance: 6.595703907427729
  - 摘要 / summary: 任务看板上的每条任务可以带若干标签（`labels`），例如 `backend`、`docs`、 `release`。看板的筛选层目前只支持按状态和负责人筛选，负责人只想看自己那条 线上带 `backend` 标签的任务时，必须在应用层自己再过滤一遍。
- **SPEC-TASKBOARD-LABEL-FILTER.md::SPEC-TASKBOARD-LABEL-FILTER.md#6. 非目标**
  - 来源 / source: `minimax/specs/SPEC-TASKBOARD-LABEL-FILTER.md`#SPEC-TASKBOARD-LABEL-FILTER.md#6. 非目标
  - 相关度 / relevance: 6.31874076177998
  - 摘要 / summary: - 不改变任务的持久化格式，不引入新的依赖。 - 不引入模糊匹配、标签的增删改或标签的层级关系。
- **SPEC-TASKBOARD-LABEL-FILTER.md::SPEC-TASKBOARD-LABEL-FILTER.md#4. 哪些原有行为必须保持（回归约束）**
  - 来源 / source: `minimax/specs/SPEC-TASKBOARD-LABEL-FILTER.md`#SPEC-TASKBOARD-LABEL-FILTER.md#4. 哪些原有行为必须保持（回归约束）
  - 相关度 / relevance: 4.839070530273602
  - 摘要 / summary: | 编号 | 原有行为 | | --- | --- | | K-1 | 任务身份在看板内唯一；重复身份被拒绝。 | | K-2 | 标签在任务创建时被去空白并转小写，`has_label` 行为不变。 | | K-3 | 任务标题、身份不得为空，估计值不得为负。 | | K-4 | `filter_tasks` 在只给状态或只给负责人时的结果与改动前一致。 | | K-5 | `group_by_status` 与 `summarize…
- **SPEC-TASKBOARD-LABEL-FILTER.md::SPEC-TASKBOARD-LABEL-FILTER.md#2. 要改什么**
  - 来源 / source: `minimax/specs/SPEC-TASKBOARD-LABEL-FILTER.md`#SPEC-TASKBOARD-LABEL-FILTER.md#2. 要改什么
  - 相关度 / relevance: 4.5507383668703945
  - 摘要 / summary: 在筛选层增加“按标签筛选”这一条件，并把该条件暴露到命令行。 - `taskboard.filters.filter_tasks` 增加可选参数 `labels`。 - `taskboard.cli` 的 `list` 子命令增加可重复的 `--label` 选项。 - 多个标签之间是“或”的关系：任务的标签集合与给定标签有交集即命中。 - 标签匹配忽略大小写与首尾空白，与 `Task.has_label` 的规则一致。

## 2. 需要修改的代码文件或符号 / code to change

- **filters.py::filter_tasks**
  - 来源 / source: `minimax/taskboard/filters.py`::filter_tasks
  - 相关度 / relevance: 0.9989140043734446
- **cli.py::build_parser**
  - 来源 / source: `minimax/taskboard/cli.py`::build_parser
  - 相关度 / relevance: 1.0849419595661378
- **filters.py**
  - 来源 / source: `minimax/taskboard/filters.py`::filters.py
  - 相关度 / relevance: 0.7353460100143513
- **cli.py**
  - 来源 / source: `minimax/taskboard/cli.py`::cli.py
  - 相关度 / relevance: 0.5638182150218027
- **cli.py::main**
  - 来源 / source: `minimax/taskboard/cli.py`::main
  - 相关度 / relevance: 0.2095086946670102
- **model.py::Task**
  - 来源 / source: `minimax/taskboard/model.py`::Task
  - 相关度 / relevance: 0.6490638981658012

## 3. 用于验证功能及兼容性的测试 / tests to run

- **test_filters.py::test_filter_tasks_with_a_label_no_task_carries_returns_nothing**
  - 来源 / source: `minimax/tests/test_filters.py`::test_filter_tasks_with_a_label_no_task_carries_returns_nothing
  - 相关度 / relevance: 1.1865810616140668
- **test_filters.py::test_filter_tasks_without_a_label_condition_keeps_every_task**
  - 来源 / source: `minimax/tests/test_filters.py`::test_filter_tasks_without_a_label_condition_keeps_every_task
  - 相关度 / relevance: 0.985086164358848
- **test_filters.py::test_filter_tasks_ignores_the_case_and_the_padding_of_a_label**
  - 来源 / source: `minimax/tests/test_filters.py`::test_filter_tasks_ignores_the_case_and_the_padding_of_a_label
  - 相关度 / relevance: 0.9224305072618187
- **test_filters.py::test_filter_tasks_matches_a_task_carrying_several_labels**
  - 来源 / source: `minimax/tests/test_filters.py`::test_filter_tasks_matches_a_task_carrying_several_labels
  - 相关度 / relevance: 0.9224305072618187
- **test_filters.py::test_filter_tasks_selects_the_tasks_carrying_one_label**
  - 来源 / source: `minimax/tests/test_filters.py`::test_filter_tasks_selects_the_tasks_carrying_one_label
  - 相关度 / relevance: 0.9224305072618187
- **test_filters.py::test_filter_tasks_combines_the_label_and_the_status_condition**
  - 来源 / source: `minimax/tests/test_filters.py`::test_filter_tasks_combines_the_label_and_the_status_condition
  - 相关度 / relevance: 0.8672685500169259

## 4. 已完成、待办与已有验证结果 / progress

- 状态 / state: 空 / empty（尚未保存任务状态）

## 5. 来源清单 / sources

- `minimax/specs/SPEC-TASKBOARD-LABEL-FILTER.md`
- `minimax/taskboard/filters.py`
- `minimax/taskboard/cli.py`
- `minimax/taskboard/model.py`
- `minimax/tests/test_filters.py`
