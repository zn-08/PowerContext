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

"""Pi branch capture keeps every visible event addressable as one Source."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import JsonValue

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    SubmitSourceObservation,
    open_builtin_contexts,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft
from powercontext.sources import (
    ConnectorBinding,
    SourceDefinitionRegistry,
    SourceRef,
    manifest_for_definition,
    project_source_for_transport,
)
from powercontext.sources.connectors import ConnectorLifecycle
from powercontext.sources.pi_session import (
    PI_EVENT_SOURCE_DEFINITION,
    PiEventInput,
    PiEventSource,
    PiSessionConnector,
    PiSessionInput,
    parse_pi_session,
    pi_event_source_id,
)
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY

_HEADER = {"type": "session", "version": 3, "id": "session-a"}


def _archive(*records: dict) -> bytes:
    return "\n".join(json.dumps(record) for record in (_HEADER, *records)).encode()


def _session(*records: dict, leaf: str | None = None) -> tuple[PiEventInput, ...]:
    return parse_pi_session(PiSessionInput(content=_archive(*records), leaf_id=leaf))


def _branch_records() -> list[dict]:
    return [
        {"type": "message", "id": "root", "parentId": None, "message": {"role": "user", "content": "Shared request"}},
        {
            "type": "message",
            "id": "left",
            "parentId": "root",
            "message": {"role": "assistant", "content": "Left evidence"},
        },
        {"type": "model_change", "id": "control", "parentId": "root"},
        {
            "type": "message",
            "id": "right",
            "parentId": "control",
            "message": {"role": "assistant", "content": "Right evidence"},
        },
    ]


def test_branch_keeps_control_nodes_in_the_ancestor_chain() -> None:
    events = _session(*_branch_records(), leaf="right")

    assert [event.event_id for event in events] == ["root", "right"]
    assert [event.content for event in events] == ["user: Shared request", "assistant: Right evidence"]
    assert events[1].parent_id == "control"
    assert events[0].parent_id is None
    assert events[1].record == {
        "type": "message",
        "id": "right",
        "parentId": "control",
        "message": {"role": "assistant", "content": "Right evidence"},
    }


def test_default_leaf_is_the_last_record_and_keeps_earlier_evidence() -> None:
    records = [
        {"type": "message", "id": "a", "parentId": None, "message": {"role": "user", "content": "First"}},
        {"type": "compaction", "id": "b", "parentId": "a", "summary": "Compacted"},
        {
            "type": "message",
            "id": "c",
            "parentId": "b",
            "message": {"role": "assistant", "content": [{"type": "text", "text": "After"}]},
        },
    ]

    events = _session(*records)

    assert [event.event_id for event in events] == ["a", "b", "c"]
    assert [event.content for event in events] == ["user: First", "compaction: Compacted", "assistant: After"]
    assert events[1].role == "compaction"
    assert events[1].event_type == "compaction"


def test_visible_bodies_follow_the_event_format() -> None:
    records = [
        {
            "type": "message",
            "id": "a",
            "parentId": None,
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "private"},
                    {"type": "toolCall", "name": "read", "id": "call-1", "arguments": {"b": 2, "a": "值"}},
                    {"type": "text", "text": "after call"},
                ],
            },
        },
        {
            "type": "message",
            "id": "b",
            "parentId": "a",
            "message": {
                "role": "toolResult",
                "toolName": "read",
                "toolCallId": "call-1",
                "content": [{"type": "text", "text": "file contents"}],
            },
        },
        {
            "type": "message",
            "id": "c",
            "parentId": "b",
            "message": {"role": "bashExecution", "command": "printf result", "output": "result", "content": []},
        },
        {
            "type": "custom_message",
            "id": "d",
            "parentId": "c",
            "content": [{"type": "text", "text": "Custom evidence"}],
        },
        {"type": "branch_summary", "id": "e", "parentId": "d", "summary": "branch summary"},
    ]

    events = _session(*records)

    assert [event.content for event in events] == [
        'assistant: [tool_call read call-1] {"a":"值","b":2}\nafter call',
        "toolResult: [tool_result read call-1] file contents",
        "bashExecution: $ printf result\nresult",
        "custom: Custom evidence",
        "branch_summary: branch summary",
    ]
    assert events[3].role == "custom"
    assert events[3].event_type == "custom_message"


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"{}\n",
        b"[]\n",
        b"null\n",
        b"\xff",
        b'{"type":"session","version":2,"id":"x"}\n',
        b'{"type":"session","version":3}\n',
    ],
)
def test_invalid_archives_fail_without_partial_results(raw: bytes) -> None:
    with pytest.raises(ValueError):
        parse_pi_session(PiSessionInput(content=raw))


@pytest.mark.parametrize(
    "records,leaf",
    [
        ([{"type": "model_change", "id": "a", "parentId": "missing"}], "a"),
        ([{"type": "model_change", "id": "a", "parentId": "a"}], "a"),
        ([{"type": "model_change", "id": "a", "parentId": None}] * 2, "a"),
        ([{"type": "model_change", "id": "a", "parentId": None}], "absent"),
    ],
)
def test_invalid_graphs_fail_without_partial_results(records: list[dict], leaf: str) -> None:
    with pytest.raises(ValueError):
        _session(*records, leaf=leaf)


def test_events_keep_a_stable_and_distinct_identity() -> None:
    registry = SourceDefinitionRegistry((PI_EVENT_SOURCE_DEFINITION,))
    events = _session(*_branch_records(), leaf="right")

    async def scenario() -> None:
        names = set()
        for event in events:
            source = await registry.resolve(event)
            assert source.name == pi_event_source_id(event.session_id, event.event_id)
            assert source.name not in names
            names.add(source.name)
            assert await registry.read(source) == event

    asyncio.run(scenario())


def test_projection_exposes_the_full_event_text() -> None:
    registry = SourceDefinitionRegistry((PI_EVENT_SOURCE_DEFINITION,))
    event = _session(*_branch_records(), leaf="left")[1]

    async def scenario() -> None:
        source = await registry.resolve(event)
        observation = project_source_for_transport(registry, source)
        evidence = observation.projection(TEXT_EVIDENCE_PROJECTION_KEY)
        assert evidence["source_type"] == "pi-event"
        assert evidence["source_id"] == source.name
        assert evidence["content"] == event.content
        assert evidence["metadata"] == {
            "session_id": "session-a",
            "event_id": "left",
            "parent_id": "root",
            "event_type": "message",
            "role": "assistant",
        }

    asyncio.run(scenario())


def test_events_survive_runtime_ingestion_and_read_back_by_reference(tmp_path: Path) -> None:
    registry = SourceDefinitionRegistry((PI_EVENT_SOURCE_DEFINITION,))
    events = _session(*_branch_records(), leaf="right")
    config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'sources.db'}"))

    async def scenario() -> None:
        async with open_builtin_runtime(config) as runtime:
            scope = await runtime.scopes.create(ScopeDraft(title="Pi", summary="Pi", idempotency_key="pi"))
            await runtime.ingestion.register(manifest_for_definition(PI_EVENT_SOURCE_DEFINITION))
            for event in events:
                source = await registry.resolve(event)
                await runtime.ingestion.submit(
                    SubmitSourceObservation(
                        scope_id=scope.scope_id,
                        observation=project_source_for_transport(registry, source),
                    )
                )
        async with open_builtin_contexts(config) as contexts:
            entries = await (await contexts.get(scope.scope_id)).sources.journal.entries()
            assert len(entries) == len(events)
            for entry, event in zip(entries, events, strict=True):
                assert entry.source_ref.source_id == pi_event_source_id(event.session_id, event.event_id)
                restored = PiEventSource.model_validate(entry.source.payload)
                assert await registry.read(restored) == event

    asyncio.run(scenario())


class _RecordingSink:
    def __init__(self) -> None:
        self.values: list[PiEventInput] = []

    async def submit(
        self,
        binding: ConnectorBinding,
        item_id: str,
        definition_name: str,
        value: object,
        /,
    ) -> SourceRef:
        del binding
        assert isinstance(value, PiEventInput)
        self.values.append(value)
        return SourceRef(source_type=definition_name, source_id=item_id)


class _MemoryCheckpoints:
    def __init__(self) -> None:
        self.value: JsonValue | None = None

    async def load(self, binding: ConnectorBinding, /) -> JsonValue | None:
        del binding
        return deepcopy(self.value)

    async def save(
        self,
        binding: ConnectorBinding,
        checkpoint: JsonValue | None,
        /,
        *,
        expected: JsonValue | None,
    ) -> None:
        del binding
        assert self.value == expected
        self.value = deepcopy(checkpoint)


def _binding() -> ConnectorBinding:
    return ConnectorBinding(
        scope_id="scope-a",
        binding_id="pi",
        connector_name="pi-session-history",
        connector_version="1",
    )


def test_connector_imports_each_branch_once_and_keeps_the_other_branch(tmp_path: Path) -> None:
    path = tmp_path / "tree.jsonl"
    path.write_bytes(_archive(*_branch_records()))

    async def scenario() -> None:
        sink = _RecordingSink()
        checkpoints = _MemoryCheckpoints()
        lifecycle = ConnectorLifecycle(sink=sink, checkpoints=checkpoints)
        binding = _binding()

        first = await lifecycle.run(PiSessionConnector(path, "left"), binding)
        assert [event.content for event in sink.values] == ["user: Shared request", "assistant: Left evidence"]
        assert first.previous_checkpoint is None
        assert first.committed_checkpoint is not None

        sink.values.clear()
        second = await lifecycle.run(PiSessionConnector(path, "right"), binding)
        assert second.previous_checkpoint == first.committed_checkpoint
        assert [event.content for event in sink.values] == ["assistant: Right evidence"]

        sink.values.clear()
        replay = await lifecycle.run(PiSessionConnector(path, "right"), binding)
        assert replay.items == ()
        assert sink.values == []

    asyncio.run(scenario())


def test_connector_appends_new_events_and_deduplicates_after_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "tree.jsonl"
    records = _branch_records()
    path.write_bytes(_archive(*records))

    async def scenario() -> None:
        sink = _RecordingSink()
        checkpoints = _MemoryCheckpoints()
        lifecycle = ConnectorLifecycle(sink=sink, checkpoints=checkpoints)
        binding = _binding()

        await lifecycle.run(PiSessionConnector(path, "right"), binding)
        sink.values.clear()

        records.append({
            "type": "message",
            "id": "tail",
            "parentId": "right",
            "message": {"role": "assistant", "content": "Later evidence"},
        })
        path.write_bytes(_archive(*records))
        added = await lifecycle.run(PiSessionConnector(path, "tail"), binding)
        assert [event.content for event in sink.values] == ["assistant: Later evidence"]

        sink.values.clear()
        replay = await lifecycle.run(PiSessionConnector(path, "tail"), binding)
        assert replay.items == ()
        assert sink.values == []
        assert replay.previous_checkpoint == added.committed_checkpoint

    asyncio.run(scenario())


def test_connector_keeps_the_checkpoint_when_the_input_is_corrupted(tmp_path: Path) -> None:
    path = tmp_path / "tree.jsonl"
    records = _branch_records()
    path.write_bytes(_archive(*records))

    async def scenario() -> None:
        sink = _RecordingSink()
        checkpoints = _MemoryCheckpoints()
        lifecycle = ConnectorLifecycle(sink=sink, checkpoints=checkpoints)
        binding = _binding()

        first = await lifecycle.run(PiSessionConnector(path, "right"), binding)
        path.write_bytes(path.read_bytes() + b"{bad json}\n")
        with pytest.raises(ValueError):
            await lifecycle.run(PiSessionConnector(path, "right"), binding)
        assert checkpoints.value == first.committed_checkpoint

        path.write_bytes(_archive(*records))
        repaired = await lifecycle.run(PiSessionConnector(path, "right"), binding)
        assert repaired.previous_checkpoint == first.committed_checkpoint
        assert repaired.items == ()

    asyncio.run(scenario())


def test_connector_refuses_a_rewritten_imported_event(tmp_path: Path) -> None:
    path = tmp_path / "tree.jsonl"
    records = _branch_records()
    path.write_bytes(_archive(*records))

    async def scenario() -> None:
        sink = _RecordingSink()
        checkpoints = _MemoryCheckpoints()
        lifecycle = ConnectorLifecycle(sink=sink, checkpoints=checkpoints)
        binding = _binding()

        first = await lifecycle.run(PiSessionConnector(path, "right"), binding)
        records[0]["message"]["content"] = "Changed request"
        path.write_bytes(_archive(*records))
        with pytest.raises(ValueError):
            await lifecycle.run(PiSessionConnector(path, "right"), binding)
        assert checkpoints.value == first.committed_checkpoint

        records[0]["message"]["content"] = "Shared request"
        path.write_bytes(_archive(*records))
        stable = await lifecycle.run(PiSessionConnector(path, "right"), binding)
        assert stable.previous_checkpoint == first.committed_checkpoint
        assert stable.items == ()

    asyncio.run(scenario())
