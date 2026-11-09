"""Send a retrieved context pack to MiniMax and keep the record.

The context pack tells a reviewer *which* material applies to a task; this
module turns it into the prompt that the model actually receives, so the
recorded exchange shows both halves: what was retrieved from seekdb and what
MiniMax answered with it.

The API key is never written down.  It is read from the environment
(``MINIMAX_API_KEY``) and only used to sign the request; the record that lands
in ``logs/minimax-api-log.jsonl`` keeps the endpoint, the model, the prompt,
the answer and the token usage, but no credential.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from minimax_ctx.config import Settings
from minimax_ctx.context import ContextPack
from minimax_ctx.materials import MaterialStore
from minimax_ctx.minimax_client import MiniMaxClient, MiniMaxResult

SYSTEM_PROMPT = (
    "你是任务看板（taskboard）项目的开发助手。"
    "你只能依据下面给出的规格、代码与测试作答，不要编造没有出现过的文件、接口或测试。"
)

DEFAULT_INSTRUCTION = (
    "请依据上面的规格、代码与测试完成这个任务。回答必须包含三部分：\n"
    "1) 你修改了哪些文件与符号，以及为什么；\n"
    "2) 修改后的完整实现，用 ```python 代码块给出（不要省略任何一行）；\n"
    "3) 需要新增或调整的测试，以及每条测试对应的规格条目编号。\n"
    "如果没有给标签选项时不收窄结果，请明确说明这一点。"
)


@dataclass(frozen=True, slots=True)
class AskOutcome:
    """The result of one recorded MiniMax exchange."""

    result: MiniMaxResult
    prompt: str
    task: str
    context_digest: str
    sources: tuple[str, ...]
    log_path: str


def _material_body(store: MaterialStore, entry: dict[str, Any]) -> str:
    """Return the full text of one material unit, or an empty string."""

    material = store.get(entry["material_id"])
    return material.content if material is not None else ""


def _section(title: str, entries: list[dict[str, Any]], store: MaterialStore) -> str:
    """Render one group of material units as a prompt section."""

    if not entries:
        return ""
    blocks: list[str] = [f"## {title}"]
    for entry in entries:
        location = entry["path"]
        if entry.get("symbol"):
            # A specification points at an anchor, code points at a symbol.
            separator = "#" if entry.get("kind") == "spec" else "::"
            location = f"{location}{separator}{entry['symbol']}"
        body = _material_body(store, entry).strip()
        blocks.append(f"### {entry['title']}\n来源 / source: {location}\n\n{body}")
    return "\n\n".join(blocks)


def build_prompt(
    store: MaterialStore,
    pack: ContextPack,
    instruction: str = DEFAULT_INSTRUCTION,
) -> str:
    """Build the prompt from the material that the context pack points at.

    The pack carries the paths, the titles and the relevance scores; the
    prompt carries the actual file bodies.  A reviewer can therefore replay
    the same retrieval and compare the two.
    """

    parts: list[str] = [
        f"# 任务 / task\n{pack.task}",
        f"# 上下文指纹 / context_digest\n{pack.digest()}",
    ]
    for title, entries in (
        ("适用的规格条目 / applicable specifications", pack.specs),
        ("需要修改的代码 / code to change", pack.code),
        ("用于验证的测试 / tests to run", pack.tests),
    ):
        rendered = _section(title, entries, store)
        if rendered:
            parts.append(rendered)
    parts.append(f"# 要求 / instruction\n{instruction}")
    return "\n\n".join(parts)


def ask(
    settings: Settings,
    store: MaterialStore,
    pack: ContextPack,
    instruction: str = DEFAULT_INSTRUCTION,
    *,
    log_dir: Path | None = None,
    max_tokens: int = 4096,
) -> AskOutcome:
    """Ask MiniMax with the pack and return the recorded outcome."""

    client = MiniMaxClient(settings, log_dir=log_dir)
    if not client.available:
        raise RuntimeError("MINIMAX_API_KEY is not set; the MiniMax API path is optional")
    prompt = build_prompt(store, pack, instruction)
    result = client.complete(
        prompt,
        system=SYSTEM_PROMPT,
        max_tokens=max_tokens,
        metadata={
            "task": pack.task,
            "context_digest": pack.digest(),
            "sources": list(pack.sources),
        },
    )
    return AskOutcome(
        result=result,
        prompt=prompt,
        task=pack.task,
        context_digest=pack.digest(),
        sources=tuple(pack.sources),
        log_path=result.log_path,
    )


def write_answer(path: Path, outcome: AskOutcome) -> Path:
    """Write the answer and its provenance into a markdown file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# MiniMax 回答记录 / recorded answer",
        "",
        f"- 任务 / task: {outcome.task}",
        f"- 上下文指纹 / context_digest: {outcome.context_digest}",
        f"- 模型 / model: {outcome.result.model}",
        f"- 用量 / usage: {outcome.result.usage}",
        f"- 停止原因 / finish_reason: {outcome.result.finish_reason or '（未提供）'}",
        f"- 调用记录 / api log: {outcome.log_path}",
        "",
        "## 检索到的来源 / sources",
        "",
    ]
    lines.extend(f"- `{source}`" for source in outcome.sources)
    lines.extend(["", "## 回答 / answer", "", outcome.result.answer, ""])
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


__all__ = [
    "AskOutcome",
    "DEFAULT_INSTRUCTION",
    "SYSTEM_PROMPT",
    "ask",
    "build_prompt",
    "write_answer",
]
