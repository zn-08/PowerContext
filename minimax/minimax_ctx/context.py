"""Assemble the task-scoped context pack.

The pack answers the four questions a development assistant needs before it
touches a file:

1. which specification entries apply to this task,
2. which code files or symbols the task is expected to change,
3. which tests prove the change and keep the old behaviour intact,
4. what has already been done, what is still open and which verification
   results already exist.

Every answer carries the source it came from, so a reviewer can open the file
and check that the statement is real.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from minimax_ctx.materials import MaterialStore
from minimax_ctx.powercontext_state import TaskState

# Identifiers, module paths and flags that a specification names.  They are the
# bridge between a specification written in prose and the code it points at.
_DOTTED_IDENTIFIER = re.compile(r"\b[a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)+\b")
_SOURCE_FILE = re.compile(r"\b[\w./-]*[\w-]+\.(?:py|md|json|toml)\b")
_FLAG = re.compile(r"--[a-z][a-z0-9-]*")


@dataclass(slots=True)
class ContextPack:
    """One task-scoped context pack."""

    task: str
    query: str
    specs: list[dict[str, Any]] = field(default_factory=list)
    code: list[dict[str, Any]] = field(default_factory=list)
    tests: list[dict[str, Any]] = field(default_factory=list)
    state: TaskState | None = None
    generated_at: str = ""

    def __post_init__(self) -> None:
        if not self.generated_at:
            self.generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    @property
    def sources(self) -> list[str]:
        """Every repository relative path the pack refers to."""

        paths: list[str] = []
        for entry in [*self.specs, *self.code, *self.tests]:
            path = entry.get("path")
            if path and path not in paths:
                paths.append(path)
        return paths

    def digest(self) -> str:
        payload = json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "query": self.query,
            "generated_at": self.generated_at,
            "specifications": self.specs,
            "code": self.code,
            "tests": self.tests,
            "state": None
            if self.state is None
            else {
                "phase": self.state.phase,
                "done": self.state.done,
                "todo": self.state.todo,
                "verification": self.state.verification,
                "updated_at": self.state.updated_at,
            },
            "sources": self.sources,
        }

    def to_markdown(self) -> str:
        lines: list[str] = []
        lines.append("# 任务上下文包 / Task Context Pack")
        lines.append("")
        lines.append(f"- 任务 / task: {self.task}")
        lines.append(f"- 检索式 / query: {self.query}")
        lines.append(f"- 生成时间 / generated_at: {self.generated_at}")
        lines.append(f"- 上下文指纹 / context_digest: {self.digest()}")
        lines.append("")

        lines.append("## 1. 本次任务适用的规格条目 / applicable specifications")
        lines.append("")
        if self.specs:
            for entry in self.specs:
                lines.append(f"- **{entry['title']}**")
                lines.append(f"  - 来源 / source: `{entry['path']}`" + (f"#{entry['symbol']}" if entry.get("symbol") else ""))
                lines.append(f"  - 相关度 / relevance: {entry.get('relevance')}")
                lines.append(f"  - 摘要 / summary: {entry.get('summary', '')}")
        else:
            lines.append("- （无 / none）")
        lines.append("")

        lines.append("## 2. 需要修改的代码文件或符号 / code to change")
        lines.append("")
        if self.code:
            for entry in self.code:
                lines.append(f"- **{entry['title']}**")
                lines.append(f"  - 来源 / source: `{entry['path']}`" + (f"::{entry['symbol']}" if entry.get("symbol") else ""))
                lines.append(f"  - 相关度 / relevance: {entry.get('relevance')}")
        else:
            lines.append("- （无 / none）")
        lines.append("")

        lines.append("## 3. 用于验证功能及兼容性的测试 / tests to run")
        lines.append("")
        if self.tests:
            for entry in self.tests:
                lines.append(f"- **{entry['title']}**")
                lines.append(f"  - 来源 / source: `{entry['path']}`" + (f"::{entry['symbol']}" if entry.get("symbol") else ""))
                lines.append(f"  - 相关度 / relevance: {entry.get('relevance')}")
        else:
            lines.append("- （无 / none）")
        lines.append("")

        lines.append("## 4. 已完成、待办与已有验证结果 / progress")
        lines.append("")
        if self.state is None:
            lines.append("- 状态 / state: 空 / empty（尚未保存任务状态）")
        else:
            lines.append(f"- 阶段 / phase: {self.state.phase}")
            lines.append(f"- 已完成 / done:")
            for item in self.state.done or ["（无 / none）"]:
                lines.append(f"  - {item}")
            lines.append(f"- 待办 / todo:")
            for item in self.state.todo or ["（无 / none）"]:
                lines.append(f"  - {item}")
            lines.append(f"- 已有验证结果 / verification:")
            if self.state.verification:
                for entry in self.state.verification:
                    lines.append(
                        f"  - {entry.get('name', 'verification')}: "
                        f"{entry.get('status', 'unknown')} "
                        f"(exit_code={entry.get('exit_code', 'n/a')}, {entry.get('command', '')})"
                    )
            else:
                lines.append("  - （无 / none）")
        lines.append("")

        lines.append("## 5. 来源清单 / sources")
        lines.append("")
        for path in self.sources:
            lines.append(f"- `{path}`")
        lines.append("")
        return "\n".join(lines)


def _summarize(content: str, limit: int = 220) -> str:
    collapsed = " ".join(content.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[:limit] + "…"


def expansion_terms(specification_texts: list[str], *, limit: int = 16) -> list[str]:
    """Return the code words that the retrieved specifications point at.

    A task is usually written in prose while the code it changes is named by
    identifiers (``taskboard.filters.filter_tasks``), by file names
    (``minimax/tests/test_filters.py``) or by option names (``--label``).  Those
    words are what a full-text index can match against, so the specification
    entries widen the query towards the code and the tests.
    """

    terms: list[str] = []

    def add(term: str) -> None:
        cleaned = term.strip().strip("`\"'").lower()
        if cleaned and cleaned not in terms:
            terms.append(cleaned)

    for text in specification_texts:
        for identifier in _DOTTED_IDENTIFIER.findall(text):
            parts = identifier.split(".")
            add(parts[-1])
            for part in parts[:-1]:
                add(part)
        for path in _SOURCE_FILE.findall(text):
            add(path.rsplit("/", 1)[-1].rsplit(".", 1)[0])
            add(path.rsplit("/", 1)[-1])
        for flag in _FLAG.findall(text):
            add(flag.lstrip("-"))
    return terms[:limit]


def expanded_query(query: str, specification_texts: list[str]) -> str:
    """Return the query used to look for the code and the tests."""

    terms = expansion_terms(specification_texts)
    if not terms:
        return query
    return f"{query} {' '.join(terms)}"


def _entry(row: dict[str, Any], store: MaterialStore) -> dict[str, Any]:
    material = store.get(row["material_id"])
    content = material.content if material else ""
    return {
        "material_id": row["material_id"],
        "kind": row["kind"],
        "path": row["path"],
        "symbol": row.get("symbol", ""),
        "title": row.get("title", ""),
        "language": row.get("language", ""),
        "sha256": row.get("sha256", ""),
        "relevance": float(row.get("relevance") or 0.0),
        "summary": _summarize(content),
    }


def _rerank(rows: list[dict[str, Any]], terms: list[str]) -> list[dict[str, Any]]:
    """Put the rows that the specifications name in front of the other hits.

    A full-text score alone mixes "the file that talks about the subject" with
    "the file that has to change".  The specification names the file and the
    symbol, so a row whose name carries one of those words is the better
    candidate.
    """

    if not terms:
        return rows

    def rank(row: dict[str, Any]) -> tuple[int, float, str]:
        path = str(row.get("path", ""))
        symbol = str(row.get("symbol", ""))
        title = str(row.get("title", ""))
        haystack = f"{path} {symbol} {title}".lower()
        matched = sum(1 for term in terms if term and term in haystack)
        relevance = float(row.get("relevance") or 0.0)
        return (-matched, -relevance, str(row.get("material_id", "")))

    return sorted(rows, key=rank)


def build_context(
    store: MaterialStore,
    *,
    task: str,
    query: str | None = None,
    state: TaskState | None = None,
    spec_limit: int = 5,
    code_limit: int = 6,
    test_limit: int = 6,
) -> ContextPack:
    """Retrieve the material that applies to ``task`` from seekdb."""

    effective_query = (query or task).strip()
    pack = ContextPack(task=task, query=effective_query, state=state)
    spec_rows = store.search(effective_query, kind="spec", limit=spec_limit)
    pack.specs = [_entry(row, store) for row in spec_rows]

    # The specifications name the code and the tests; widen the query with
    # those names before looking for them.
    specification_texts = [
        material.content for material in (store.get(row["material_id"]) for row in spec_rows) if material is not None
    ]
    widened = expanded_query(effective_query, specification_texts)
    terms = expansion_terms(specification_texts)
    code_rows = store.search(widened, kind="code", limit=max(code_limit * 3, code_limit))
    test_rows = store.search(widened, kind="test", limit=max(test_limit * 3, test_limit))
    pack.code = [_entry(row, store) for row in _rerank(code_rows, terms)[:code_limit]]
    pack.tests = [_entry(row, store) for row in _rerank(test_rows, terms)[:test_limit]]
    return pack


__all__ = ["ContextPack", "build_context"]
