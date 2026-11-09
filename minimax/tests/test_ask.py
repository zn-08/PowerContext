"""Tests for the context-to-prompt bridge.

The prompt is the contract between what seekdb retrieved and what the model
receives, so these tests check that the retrieved bodies really travel with
the request and that the recorded metadata ties the answer to that context.
"""

from __future__ import annotations

from minimax_ctx.ask import DEFAULT_INSTRUCTION, build_prompt
from minimax_ctx.context import ContextPack
from minimax_ctx.materials import Material


class _FakeStore:
    """A material store that serves two fixed units."""

    def __init__(self) -> None:
        self.units = {
            "spec:minimax/specs/SPEC.md": Material(
                material_id="spec:minimax/specs/SPEC.md",
                kind="spec",
                path="minimax/specs/SPEC.md",
                title="SPEC-TASKBOARD-LABEL-FILTER",
                content="L-1 labels 是可选参数。",
                symbol="1. 背景",
            ),
            "code:minimax/taskboard/filters.py": Material(
                material_id="code:minimax/taskboard/filters.py",
                kind="code",
                path="minimax/taskboard/filters.py",
                title="filters.py",
                content="def filter_tasks(tasks, *, status=None, assignee=None):\n    ...\n",
                symbol="filter_tasks",
            ),
        }

    def get(self, material_id: str) -> Material | None:
        return self.units.get(material_id)


def _pack() -> ContextPack:
    return ContextPack(
        task="为任务看板增加按标签筛选任务的能力",
        query="为任务看板增加按标签筛选任务的能力",
        specs=[
            {
                "material_id": "spec:minimax/specs/SPEC.md",
                "kind": "spec",
                "path": "minimax/specs/SPEC.md",
                "symbol": "1. 背景",
                "title": "SPEC-TASKBOARD-LABEL-FILTER",
                "relevance": 7.41,
            }
        ],
        code=[
            {
                "material_id": "code:minimax/taskboard/filters.py",
                "kind": "code",
                "path": "minimax/taskboard/filters.py",
                "symbol": "filter_tasks",
                "title": "filters.py",
                "relevance": 0.61,
            }
        ],
    )


def test_the_prompt_carries_the_task_and_the_instruction() -> None:
    prompt = build_prompt(_FakeStore(), _pack())
    assert "为任务看板增加按标签筛选任务的能力" in prompt
    assert DEFAULT_INSTRUCTION in prompt


def test_the_prompt_carries_the_retrieved_bodies_and_their_sources() -> None:
    prompt = build_prompt(_FakeStore(), _pack())
    assert "L-1 labels 是可选参数。" in prompt
    assert "def filter_tasks(tasks, *, status=None, assignee=None):" in prompt
    assert "minimax/specs/SPEC.md#1. 背景" in prompt
    assert "minimax/taskboard/filters.py::filter_tasks" in prompt


def test_the_prompt_carries_the_context_digest() -> None:
    pack = _pack()
    prompt = build_prompt(_FakeStore(), pack)
    assert pack.digest() in prompt


def test_a_custom_instruction_replaces_the_default_one() -> None:
    prompt = build_prompt(_FakeStore(), _pack(), "只回答一句话。")
    assert "只回答一句话。" in prompt
    assert DEFAULT_INSTRUCTION not in prompt


def test_the_context_digest_does_not_depend_on_the_generation_time() -> None:
    first = _pack()
    second = _pack()
    second.generated_at = "2030-01-01T00:00:00+00:00"
    assert first.digest() == second.digest()


def test_the_context_digest_changes_with_the_retrieved_material() -> None:
    first = _pack()
    second = _pack()
    second.code = []
    assert first.digest() != second.digest()
