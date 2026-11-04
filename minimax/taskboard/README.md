# taskboard — 任务看板

一个很小但完整的业务工程：领域模型、看板存储、筛选层和命令行前端，全部有测试。
它是本目录中 SDD 上下文模块的“被开发对象”——上下文模块把这里的规格、代码和
测试导入 seekdb，并为一次真实修改检索出可用的上下文。

## 目录

| 路径 | 作用 |
| --- | --- |
| `model.py` | `Task` 领域模型与 `TaskStatus` 状态机 |
| `store.py` | `TaskStore`：看板存储、身份唯一性、JSON 存取 |
| `filters.py` | 筛选层：按状态、负责人（以及标签）选择任务 |
| `cli.py` | 命令行前端：`taskboard list` / `taskboard summary` |

## 运行

```bash
cd minimax
python -m taskboard.cli list --board board.json --status in_progress
python -m taskboard.cli summary --board board.json --json
```

## 测试

```bash
cd minimax
python -m pytest tests -q
```

## 规格

- `minimax/specs/SPEC-TASKBOARD-LABEL-FILTER.md`：本次修改的规格。
- `minimax/specs/SPEC-CONTEXT-MODULE.md`：上下文模块自身的规格。
