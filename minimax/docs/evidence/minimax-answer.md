# MiniMax 回答记录 / recorded answer

- 任务 / task: 为任务看板增加按标签筛选任务的能力
- 上下文指纹 / context_digest: 4a0df2a81f686624de601aa92d0db11874704e265c7587ceab1d694c00f6df17
- 模型 / model: MiniMax-M3.1-Flash
- 用量 / usage: {'total_tokens': 15785, 'total_characters': 0, 'prompt_tokens': 2989, 'completion_tokens': 12796, 'completion_tokens_details': {'reasoning_tokens': 10489}, 'prompt_tokens_details': {'cached_tokens': 0}}
- 停止原因 / finish_reason: stop
- 调用记录 / api log: D:\A_BIT\学习\大三上\Occeanbase高级数据库技术\比赛\work\eco\minimax\logs\minimax-api-log.jsonl

## 检索到的来源 / sources

- `minimax/specs/SPEC-TASKBOARD-LABEL-FILTER.md`
- `minimax/taskboard/filters.py`
- `minimax/taskboard/cli.py`
- `minimax/taskboard/model.py`
- `minimax/tests/test_filters.py`

## 回答 / answer

## 1) 修改的文件、符号及原因

| 文件 | 符号 | 修改内容 | 原因 |
|---|---|---|---|
| `minimax/taskboard/filters.py` | `filter_tasks` | 增加可选关键字参数 `labels: Sequence[str] \| None = None`；将标签去空白、转小写并忽略空白标签；多个标签按“或”匹配 | 实现规格 §2 的标签筛选，同时保持状态、负责人筛选语义不变 |
| `minimax/taskboard/filters.py` | 导入语句 | 导入 `Sequence` | 为 `labels` 参数提供类型标注 |
| `minimax/taskboard/cli.py` | `build_parser` | 为 `list` 子命令增加可重复的 `--label` 参数 | 暴露规格 §2 要求的命令行标签筛选能力 |
| `minimax/taskboard/cli.py` | `main` | 调用 `filter_tasks` 时传入 `labels=args.label or None` | 将命令行标签条件传递给筛选层；未提供 `--label` 时不收窄结果 |

没有修改 `model.py`、存储层或 JSON 输出，因此：

- 标签创建时的规范化及 `Task.has_label` 行为保持不变，对应 K-2。
- 状态、负责人筛选保持不变，对应 K-4。
- `group_by_status`、`summarize` 不受影响，对应 K-5。
- JSON 存取格式不变，对应 K-6。
- 未增加依赖，也未改变持久化格式，符合 §6。

**明确行为：**

- 不传 `labels`，或传入空序列：不收窄结果。
- 只传入空白标签：不构成筛选条件，不收窄结果。
- CLI 不传 `--label` 时，`args.label` 为 `[]`，随后转换为 `None`，不收窄结果。
- 多个标签之间是“或”；标签条件与状态、负责人条件之间是“与”。

## 2) 修改后的完整实现

以下完整列出所有被修改符号的实现，不使用省略占位。题面未提供的 `group_by_status`、`summarize` 和 `_load_board` 的既有正文保持原样，不作臆造。

### `minimax/taskboard/filters.py`

```python
"""Selection helpers for a task board."""
from __future__ import annotations

from typing import Iterable, Sequence

from taskboard.model import Task, TaskStatus

__all__ = ["filter_tasks", "group_by_status", "summarize"]


def filter_tasks(
    tasks: Iterable[Task],
    *,
    status: TaskStatus | None = None,
    assignee: str | None = None,
    labels: Sequence[str] | None = None,
) -> list[Task]:
    """Return the tasks that match every given condition.

    A condition that is not given does not narrow the result.  Several labels
    are an "or": a task matches when it carries any of them.  Labels are
    compared the way :meth:`Task.has_label` compares them, so the case and the
    padding of a label do not matter.  A label that is blank carries no
    condition and is ignored.
    """

    selected = list(tasks)
    if status is not None:
        selected = [task for task in selected if task.status == status]
    if assignee is not None:
        selected = [task for task in selected if task.assignee == assignee]
    if labels:
        wanted = {label.strip().lower() for label in labels if label.strip()}
        if wanted:
            selected = [task for task in selected if wanted.intersection(task.labels)]
    return selected
```

### `minimax/taskboard/cli.py`

```python
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="taskboard", description="Manage one task board.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List the tasks of the board.")
    list_parser.add_argument("--board", default=None, help="Path of the board JSON file.")
    list_parser.add_argument("--status", choices=[str(status) for status in TaskStatus], default=None)
    list_parser.add_argument("--assignee", default=None)
    list_parser.add_argument(
        "--label",
        action="append",
        default=[],
        help="Only list the tasks carrying this label; repeat for several labels.",
    )
    list_parser.add_argument("--json", action="store_true", help="Write the tasks as JSON.")

    summary_parser = subparsers.add_parser("summary", help="Count the tasks per status.")
    summary_parser.add_argument("--board", default=None, help="Path of the board JSON file.")
    summary_parser.add_argument("--json", action="store_true", help="Write the summary as JSON.")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        store = _load_board(args.board)
        if args.command == "list":
            tasks = filter_tasks(
                store.list(),
                status=TaskStatus(args.status) if args.status else None,
                assignee=args.assignee,
                labels=args.label or None,
            )
            if args.json:
                print(
                    json.dumps(
                        [
                            {
                                "id": task.id,
                                "title": task.title,
                                "status": str(task.status),
                                "assignee": task.assignee,
                                "labels": list(task.labels),
                                "estimate": task.estimate,
                            }
                            for task in tasks
                        ],
                        ensure_ascii=False,
                        indent=2,
                    )
                )
            else:
                for task in tasks:
                    print(f"{task.id}\t{task.status}\t{task.title}")
            return 0
        if args.command == "summary":
            summary = summarize(store.list())
            if args.json:
                print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                for status, count in sorted(summary.items()):
                    print(f"{status}\t{count}")
            return 0
    except TaskBoardError as error:
        print(f"error: {error}")
        return 1
    parser.error("unknown command")
    return 2
```

## 3) 需要新增或调整的测试及规格映射

题面给出的以下六条测试应保留并全部运行，现有断言无需调整：

| 测试 | 验证内容 | 规格条目 |
|---|---|---|
| `test_filter_tasks_with_a_label_no_task_carries_returns_nothing` | 没有任务携带指定标签时返回空列表 | `SPEC-TASKBOARD-LABEL-FILTER §2` |
| `test_filter_tasks_without_a_label_condition_keeps_every_task` | 未提供标签条件时保留所有任务 | `SPEC-TASKBOARD-LABEL-FILTER §2`、K-4 |
| `test_filter_tasks_ignores_the_case_and_the_padding_of_a_label` | 标签匹配忽略大小写和首尾空白 | `SPEC-TASKBOARD-LABEL-FILTER §2`、K-2 |
| `test_filter_tasks_matches_a_task_carrying_several_labels` | 携带多个标签的任务可按其中任一标签命中 | `SPEC-TASKBOARD-LABEL-FILTER §2` |
| `test_filter_tasks_selects_the_tasks_carrying_one_label` | 按单个标签筛选任务 | `SPEC-TASKBOARD-LABEL-FILTER §2` |
| `test_filter_tasks_combines_the_label_and_the_status_condition` | 标签条件与状态条件同时生效 | `SPEC-TASKBOARD-LABEL-FILTER §2`、K-4 |

对应测试代码为：

```python
def test_filter_tasks_with_a_label_no_task_carries_returns_nothing() -> None:
    assert filter_tasks(_tasks(), labels=["frontend"]) == []


def test_filter_tasks_without_a_label_condition_keeps_every_task() -> None:
    assert [task.id for task in filter_tasks(_tasks())] == ["T-1", "T-2", "T-3"]


def test_filter_tasks_ignores_the_case_and_the_padding_of_a_label() -> None:
    selected = filter_tasks(_tasks(), labels=[" BACKEND "])
    assert [task.id for task in selected] == ["T-2"]


def test_filter_tasks_matches_a_task_carrying_several_labels() -> None:
    selected = filter_tasks(_tasks(), labels=["api"])
    assert [task.id for task in selected] == ["T-2"]


def test_filter_tasks_selects_the_tasks_carrying_one_label() -> None:
    selected = filter_tasks(_tasks(), labels=["backend"])
    assert [task.id for task in selected] == ["T-2"]


def test_filter_tasks_combines_the_label_and_the_status_condition() -> None:
    selected = filter_tasks(_tasks(), labels=["backend"], status=TaskStatus.IN_PROGRESS)
    assert [task.id for task in selected] == ["T-2"]
```

建议补充以下覆盖，以明确验证“多个标签为或关系”、空白标签和 CLI 重复选项：

| 建议测试 | 规格条目 |
|---|---|
| `test_filter_tasks_treats_several_labels_as_or` | `SPEC-TASKBOARD-LABEL-FILTER §2` |
| `test_filter_tasks_ignores_blank_label_conditions` | `SPEC-TASKBOARD-LABEL-FILTER §2` |
| `test_list_parser_collects_repeated_label_options` | `SPEC-TASKBOARD-LABEL-FILTER §2` |
| `test_list_parser_without_label_keeps_empty_label_list` | `SPEC-TASKBOARD-LABEL-FILTER §2`、K-4 |

```python
def test_filter_tasks_treats_several_labels_as_or() -> None:
    selected = filter_tasks(_tasks(), labels=["backend", "frontend"])
    assert [task.id for task in selected] == ["T-2"]


def test_filter_tasks_ignores_blank_label_conditions() -> None:
    selected = filter_tasks(_tasks(), labels=["", "   "])
    assert [task.id for task in selected] == ["T-1", "T-2", "T-3"]
```

CLI 解析测试可加入现有 CLI 测试模块，并导入 `build_parser`：

```python
from taskboard.cli import build_parser


def test_list_parser_collects_repeated_label_options() -> None:
    args = build_parser().parse_args(
        ["list", "--label", "backend", "--label", "docs"]
    )
    assert args.label == ["backend", "docs"]


def test_list_parser_without_label_keeps_empty_label_list() -> None:
    args = build_parser().parse_args(["list"])
    assert args.label == []
```

其中 CLI 不提供 `--label` 时，解析结果为 `[]`，`main` 将其作为 `labels=None` 传给 `filter_tasks`，因此不会收窄任务列表。
