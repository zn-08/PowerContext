# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Measure the net transmission saving of the incremental Context Pack protocol.

The measurement follows the contract of the ``context-02-incremental-pack``
challenge:

* ``raw_full_bytes`` is the sum of the UTF-8 byte length of every round's
  plain ``prepare`` body;
* ``incremental_bytes`` is the sum of ``len(payload.to_bytes())`` over the
  first complete pack and every following delta, so JSON, Base64, digests and
  every other protocol field are counted;
* the two observation baselines are the cumulative size of the complete v1
  pack for every round and the cumulative size of
  ``gzip.compress(body, compresslevel=9, mtime=0)`` for every round.

The published workload holds two meeting turns, which is too few for the
40-round sequence. Point ``--workload`` at the pinned QMSum meeting file to
measure the full sequence::

    python scripts/measure_incremental_pack.py --workload /path/to/ES2004a.json \\
        --first-turn 184 --turn-count 120
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
from dataclasses import dataclass, field
from pathlib import Path

from powercontext.builtin.artifacts.memory import MemoryCandidateRequest, MemoryEntryInput
from powercontext.builtin.runtime import BuiltinConfig, CaptureSource, PrepareContextRequest, open_builtin_runtime
from powercontext.builtin.runtime.context_pack import ContextPack, apply_context_pack_delta
from powercontext.builtin.scope import ScopeDraft
from powercontext.builtin.sources import ContentSource

DEFAULT_WORKLOAD = Path("competition/challenges/context-02-incremental-pack/workload/public/turns.json")


class _ContentCandidatePipeline:
    """Turn every captured Source into one conversation-turn Memory entry."""

    async def extract(self, request: MemoryCandidateRequest, /) -> tuple[MemoryEntryInput, ...]:
        return tuple(
            MemoryEntryInput(kind="conversation-turn", text=source.content, sources=(source,))
            for source in request.sources
            if isinstance(source, ContentSource)
        )


@dataclass
class _Sequence:
    """One measurement sequence over real meeting turns."""

    name: str
    turns: list[str]
    queries: list[str]
    rounds: int = 40
    initial_turns: int = 8
    append_every: int = 0
    query_every: int = 0
    max_bytes: int = 4096
    events: list[tuple[int, str]] = field(default_factory=list)


def _load_turns(path: Path) -> tuple[list[str], list[str]]:
    """Return the meeting turns and the retrieval terms of one workload file."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if "meeting_transcripts" in payload:
        turns = [f"{turn['speaker']}: {turn['content']}" for turn in payload["meeting_transcripts"]]
        return turns, ["remote", "design", "control"]
    turns = [turn["text"] for turn in payload["turns"]]
    return turns, [payload.get("query", "remote")]


def _build_sequences(turns: list[str], terms: list[str]) -> list[_Sequence]:
    """Build the measured sequences: steady, mixed, and the worst case."""
    usable = [term for term in terms if term] or ["remote"]
    return [
        _Sequence(
            name="steady (3 append moments, 1 retrieval term)",
            turns=turns,
            queries=usable[:1],
            append_every=13,
        ),
        _Sequence(
            name="mixed (append every 4 rounds, 2 retrieval terms)",
            turns=turns,
            queries=usable[:2] if len(usable) > 1 else usable,
            append_every=4,
            query_every=7,
        ),
        _Sequence(
            name="worst case (append every round)",
            turns=turns,
            queries=usable[:1],
            append_every=1,
        ),
    ]


async def _measure(sequence: _Sequence) -> dict[str, object]:
    """Run one sequence and return its byte accounting."""
    raw_full_bytes = 0
    incremental_bytes = 0
    full_pack_bytes = 0
    gzip_bytes = 0
    distinct_contexts: set[bytes] = set()
    empty_rounds = 0
    base: ContextPack | None = None
    captured = 0

    async with open_builtin_runtime(BuiltinConfig(), candidate_pipeline=_ContentCandidatePipeline()) as runtime:
        scope = await runtime.scopes.create(
            ScopeDraft(
                title="Incremental pack measurement",
                summary="Incremental pack measurement",
                idempotency_key=f"measure-{sequence.name}",
            )
        )
        sources = runtime.sources.for_scope(scope.scope_id)
        memory = runtime.memory.for_scope(scope.scope_id)
        context = runtime.context.for_scope(scope.scope_id)

        async def capture_next() -> None:
            nonlocal captured
            if captured >= len(sequence.turns):
                return
            await sources.capture(
                CaptureSource(source_id=f"turn-{captured}", content=sequence.turns[captured], metadata={})
            )
            captured += 1
            while (await memory.flush()).processed:
                pass

        for _ in range(sequence.initial_turns):
            await capture_next()

        for index in range(sequence.rounds):
            if sequence.append_every and index and index % sequence.append_every == 0:
                await capture_next()
            query = sequence.queries[0]
            if sequence.query_every and index and index % sequence.query_every == 0:
                query = sequence.queries[index % len(sequence.queries)]
            request = PrepareContextRequest(query=query, max_bytes=sequence.max_bytes)
            plain = await context.prepare(request)
            body = (plain.content or "").encode("utf-8")
            payload = await context.prepare_incremental(request, base_pack=base)
            base = payload if isinstance(payload, ContextPack) else apply_context_pack_delta(base, payload)
            raw_full_bytes += len(body)
            incremental_bytes += len(payload.to_bytes())
            full_pack_bytes += len(ContextPack.from_content(plain.content or "").to_bytes())
            gzip_bytes += len(gzip.compress(body, compresslevel=9, mtime=0))
            distinct_contexts.add(body)
            empty_rounds += int(not body)

    return {
        "name": sequence.name,
        "rounds": sequence.rounds,
        "raw_full_bytes": raw_full_bytes,
        "incremental_bytes": incremental_bytes,
        "full_pack_bytes": full_pack_bytes,
        "gzip_bytes": gzip_bytes,
        "distinct_contexts": len(distinct_contexts),
        "empty_rounds": empty_rounds,
        "saving_vs_raw": 1 - incremental_bytes / raw_full_bytes if raw_full_bytes else 0.0,
        "saving_vs_full_pack": 1 - incremental_bytes / full_pack_bytes if full_pack_bytes else 0.0,
        "saving_vs_gzip": 1 - incremental_bytes / gzip_bytes if gzip_bytes else 0.0,
    }


async def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workload", type=Path, default=DEFAULT_WORKLOAD, help="workload or meeting JSON")
    parser.add_argument("--first-turn", type=int, default=0, help="index of the first meeting turn")
    parser.add_argument("--turn-count", type=int, default=120, help="number of meeting turns to use")
    arguments = parser.parse_args()
    turns, terms = _load_turns(arguments.workload)
    if "meeting_transcripts" in json.loads(arguments.workload.read_text(encoding="utf-8")):
        turns = turns[arguments.first_turn : arguments.first_turn + arguments.turn_count]
    for sequence in _build_sequences(turns, terms):
        print(json.dumps(await _measure(sequence), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(_main())
