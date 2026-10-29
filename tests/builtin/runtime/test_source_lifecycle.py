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

"""Source withdrawal and restore across the native Runtime surfaces."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.artifacts.memory.service import MemoryService
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.records import ArtifactWrite
from powercontext.builtin.runtime import (
    BuiltinConfig,
    CaptureSource,
    ContextAssembly,
    ContextAssemblySection,
    GetMemoryEntryRequest,
    GetTopicMemoryRequest,
    PrepareContextRequest,
    RememberMemoryRequest,
    SearchMemoryRequest,
    SearchTopicMemoryRequest,
    open_builtin_runtime,
)
from powercontext.builtin.runtime.source_lifecycle import SourceLifecycleError, SourceLifecycleEvent
from powercontext.builtin.scope import ScopeDraft


def _event(node_id: str, sequence: int, available: bool, revision: int = 1) -> SourceLifecycleEvent:
    return SourceLifecycleEvent(
        node_id=node_id,
        revision=revision,
        sequence=sequence,
        available=available,
        reason="controlled lifecycle",
    )


async def _build(runtime, suffix: str = "primary"):
    """Register one evidence graph and return its scope, service, and bindings."""

    scope = await runtime.scopes.create(
        ScopeDraft(title="Lifecycle", summary="Dependency evidence", idempotency_key=suffix)
    )
    scope_id = scope.scope_id
    lifecycle = runtime.lifecycle.for_scope(scope_id)
    citations = {}
    for node in ("source-a", "source-b", "source-c"):
        receipt = await runtime.sources.for_scope(scope_id).capture(
            CaptureSource(source_id=node, content=f"Origin {node}", metadata={})
        )
        await lifecycle.register_source(node, receipt.source_ref, revision=1)
        citations[node] = receipt.source_ref
    for node, parents, policy in (
        ("memory-all", ("source-a", "source-b"), "all"),
        ("memory-any", ("source-a", "source-b"), "any"),
        ("memory-deep", ("memory-all",), "all"),
        ("memory-independent", ("source-c",), "all"),
    ):
        created = await runtime.memory.for_scope(scope_id).remember(
            RememberMemoryRequest(entries=(MemoryEntryInput(kind="fact", text=f"{node} fixture evidence"),))
        )
        await lifecycle.register_memory(node, created.entry.citation, parents=parents, policy=policy)
        citations[node] = created.entry.citation
    artifact = await runtime.records.for_scope(scope_id).create_artifact(
        "topic-memory",
        ArtifactWrite(
            content={
                "title": "topic-shared",
                "summary": "topic-shared supported summary",
                "detail": "topic-shared detailed evidence",
            }
        ),
    )
    reference = ArtifactRef(family="topic-memory", artifact_id=artifact.artifact_id, revision=artifact.revision)
    await lifecycle.register_topic(
        "topic-shared",
        reference,
        parents=("memory-any", "memory-independent"),
        policy="all",
    )
    citations["topic-shared"] = reference
    return scope_id, lifecycle, citations


async def _is_available(lifecycle, node_id: str) -> bool:
    try:
        await lifecycle.resolve(node_id, purpose="generation")
    except SourceLifecycleError:
        return False
    return True


async def _assert_native_state(runtime, scope_id, node_id: str, citations, available: bool) -> None:
    """Assert that list, search, Topic search, and Context agree on availability."""

    memory = runtime.memory.for_scope(scope_id)
    entries = await memory.list(include_inactive=True)
    states = {record.entry.text.split()[0]: record.state for record in entries.entries}
    if node_id in states:
        assert (states[node_id] == "active") is available
        page = await memory.search(SearchMemoryRequest(query=node_id, mode="fts", limit=10))
        assert any(hit.text == f"{node_id} fixture evidence" for hit in page.hits) is available
        original = await memory.get(GetMemoryEntryRequest(citation=citations[node_id]))
        assert original.entry.text == f"{node_id} fixture evidence"
    else:
        page = await runtime.topic_memory.for_scope(scope_id).search(SearchTopicMemoryRequest(query=node_id, limit=20))
        assert bool(page.hits) is available
        record = await runtime.records.for_scope(scope_id).get_artifact_revision(
            "topic-memory",
            citations[node_id].artifact_id,
            citations[node_id].revision,
        )
        assert record.content["title"] == node_id
        published = await runtime.topic_memory.for_scope(scope_id).get(
            GetTopicMemoryRequest(artifact=citations[node_id])
        )
        assert published.topic.content.title == node_id
    prepared = await runtime.context.for_scope(scope_id).prepare(
        PrepareContextRequest(
            query=node_id,
            max_bytes=4096,
            assembly=ContextAssembly(
                sections=(
                    ContextAssemblySection(family="memory", limit=6),
                    ContextAssemblySection(family="topic-memory", limit=2),
                )
            ),
        )
    )
    expected = f"{node_id} fixture evidence" if node_id.startswith("memory") else f"{node_id} supported summary"
    assert (expected in (prepared.content or "")) is available


def test_registration_resolves_state_from_parents_and_rejects_conflicting_bindings() -> None:
    async def run():
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            _scope_id, lifecycle, citations = await _build(runtime)
            for node_id in ("source-a", "memory-all", "memory-any", "memory-deep", "topic-shared"):
                assert await _is_available(lifecycle, node_id)

            # The same registration is idempotent.
            await lifecycle.register_source("source-a", citations["source-a"], revision=1)
            await lifecycle.register_memory(
                "memory-any",
                citations["memory-any"],
                parents=("source-a", "source-b"),
                policy="any",
            )
            # A different binding for one node id is a conflict.
            with pytest.raises(SourceLifecycleError):
                await lifecycle.register_source("source-a", citations["source-b"], revision=1)
            with pytest.raises(SourceLifecycleError):
                await lifecycle.register_memory(
                    "memory-any",
                    citations["memory-any"],
                    parents=("source-a",),
                    policy="all",
                )
            with pytest.raises(SourceLifecycleError):
                await lifecycle.register_memory(
                    "memory-new",
                    citations["memory-any"],
                    parents=("source-missing",),
                    policy="all",
                )
            with pytest.raises(SourceLifecycleError):
                await lifecycle.register_memory(
                    "memory-new",
                    citations["memory-any"],
                    parents=("memory-new",),
                    policy="all",
                )
            with pytest.raises(SourceLifecycleError):
                await lifecycle.register_memory("memory-new", citations["memory-any"], parents=(), policy="all")
            with pytest.raises(SourceLifecycleError):
                await lifecycle.register_memory(
                    "memory-new",
                    citations["memory-any"],
                    parents=("source-a",),
                    policy="either",
                )

    asyncio.run(run())


def test_withdrawal_follows_all_and_any_policies_into_the_native_surfaces() -> None:
    async def run():
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            scope_id, lifecycle, citations = await _build(runtime)

            withdrawn = await lifecycle.apply(_event("source-a", 1, False))
            assert withdrawn == sorted({
                "source-b",
                "source-c",
                "memory-any",
                "memory-independent",
                "topic-shared",
            })
            assert not await _is_available(lifecycle, "memory-all")
            assert not await _is_available(lifecycle, "memory-deep")
            assert await _is_available(lifecycle, "memory-any")
            assert await _is_available(lifecycle, "memory-independent")
            for node_id in ("memory-all", "memory-deep", "memory-any", "memory-independent", "topic-shared"):
                await _assert_native_state(
                    runtime, scope_id, node_id, citations, await _is_available(lifecycle, node_id)
                )

            withdrawn = await lifecycle.apply(_event("source-b", 1, False))
            assert withdrawn == sorted({"source-c", "memory-independent"})
            assert not await _is_available(lifecycle, "memory-any")
            assert not await _is_available(lifecycle, "topic-shared")
            for node_id in ("memory-any", "memory-independent", "topic-shared"):
                await _assert_native_state(
                    runtime, scope_id, node_id, citations, await _is_available(lifecycle, node_id)
                )

            restored = await lifecycle.apply(_event("source-a", 2, True))
            assert "memory-any" in restored
            assert "topic-shared" in restored
            # The "all" policy still waits for the withdrawn second Source.
            assert "memory-all" not in restored
            assert "memory-deep" not in restored
            for node_id in ("memory-all", "memory-deep", "memory-any", "topic-shared"):
                await _assert_native_state(
                    runtime, scope_id, node_id, citations, await _is_available(lifecycle, node_id)
                )

            restored = await lifecycle.apply(_event("source-b", 2, True))
            assert "memory-all" in restored
            assert "memory-deep" in restored
            assert "memory-any" in restored
            for node_id in ("memory-all", "memory-deep", "memory-any", "topic-shared"):
                await _assert_native_state(
                    runtime, scope_id, node_id, citations, await _is_available(lifecycle, node_id)
                )

    asyncio.run(run())


def test_generation_requires_support_while_audit_keeps_the_original_reference() -> None:
    async def run():
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            scope_id, lifecycle, citations = await _build(runtime)
            assert await lifecycle.resolve("memory-all", purpose="generation") == citations["memory-all"]
            assert await lifecycle.resolve("topic-shared", purpose="audit") == citations["topic-shared"]

            await lifecycle.apply(_event("source-a", 1, False))
            with pytest.raises(SourceLifecycleError):
                await lifecycle.resolve("memory-all", purpose="generation")
            assert await lifecycle.resolve("memory-all", purpose="audit") == citations["memory-all"]
            with pytest.raises(SourceLifecycleError):
                await lifecycle.resolve("unknown-node", purpose="audit")
            with pytest.raises(SourceLifecycleError):
                await lifecycle.resolve("memory-all", purpose="summary")

            await lifecycle.apply(_event("source-a", 2, True))
            assert await lifecycle.resolve("memory-all", purpose="generation") == citations["memory-all"]
            original = await runtime.memory.for_scope(scope_id).get(
                GetMemoryEntryRequest(citation=citations["memory-all"])
            )
            assert original.entry.text == "memory-all fixture evidence"

    asyncio.run(run())


def test_state_survives_restart_and_scopes_stay_isolated(tmp_path: Path) -> None:
    async def run():
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'lifecycle.db'}"))
        async with open_builtin_runtime(config) as runtime:
            scope_id, lifecycle, _citations = await _build(runtime)
            other_scope, other_lifecycle, _ = await _build(runtime, suffix="secondary")
            await lifecycle.apply(_event("source-a", 1, False))
            assert not await _is_available(lifecycle, "memory-all")
            assert await _is_available(other_lifecycle, "memory-all")

        async with open_builtin_runtime(config) as runtime:
            restarted = runtime.lifecycle.for_scope(scope_id)
            assert not await _is_available(restarted, "memory-all")
            assert await _is_available(restarted, "memory-independent")
            assert await _is_available(runtime.lifecycle.for_scope(other_scope), "memory-all")
            with pytest.raises(SourceLifecycleError):
                await restarted.apply(_event("source-a", 1, True))
            with pytest.raises(SourceLifecycleError):
                await restarted.apply(_event("source-missing", 1, True))
            with pytest.raises(SourceLifecycleError):
                await restarted.apply(_event("source-a", 2, True, revision=2))
            await restarted.apply(_event("source-a", 2, True))
            with pytest.raises(SourceLifecycleError):
                await restarted.apply(_event("source-a", 1, False))

    asyncio.run(run())


def test_replay_adds_no_memory_revision_and_retry_completes_an_interrupted_propagation() -> None:
    async def run():
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            scope_id, lifecycle, citations = await _build(runtime)
            await lifecycle.apply(_event("source-a", 1, False))
            before = (await runtime.memory.for_scope(scope_id).list()).memory_ref
            replayed = await lifecycle.apply(_event("source-a", 1, False))
            assert replayed == sorted({"source-b", "source-c", "memory-any", "memory-independent", "topic-shared"})
            assert (await runtime.memory.for_scope(scope_id).list()).memory_ref == before

            async def failing_forget(self, memory, *, entries, reason=None):
                raise RuntimeError("simulated Memory write failure")  # noqa: TRY003

            with patch.object(MemoryService, "forget", failing_forget), pytest.raises(RuntimeError):
                await lifecycle.apply(_event("source-b", 1, False))
            assert await _is_available(lifecycle, "memory-any")

            retried = await lifecycle.apply(_event("source-b", 1, False))
            assert retried == sorted({"source-c", "memory-independent"})
            assert not await _is_available(lifecycle, "memory-any")
            await _assert_native_state(runtime, scope_id, "memory-any", citations, False)

    asyncio.run(run())


def test_event_fields_are_validated() -> None:
    with pytest.raises(ValidationError):
        SourceLifecycleEvent(node_id="x", revision=0, sequence=0, available=False, reason="", extra=True)
    with pytest.raises(ValidationError):
        SourceLifecycleEvent(node_id="  ", revision=1, sequence=1, available=False, reason="reason")
    with pytest.raises(ValidationError):
        SourceLifecycleEvent(node_id="x", revision=1, sequence=1, available=False, reason=" ")
    with pytest.raises(ValidationError):
        SourceLifecycleEvent(node_id="x", revision="1", sequence=1, available=False, reason="reason")
    assert issubclass(SourceLifecycleError, ValueError)
