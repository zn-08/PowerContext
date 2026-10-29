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

"""Source lifecycle events and the scope-bound evidence dependency service.

One scope owns one evidence dependency graph. Every node is a Source, a Memory
entry, or a Topic Memory artifact, and every derived node names the parents
that support it. A Source lifecycle event withdraws or restores one Source, and
the availability of every dependent node follows the registered ``all`` or
``any`` policy.

Availability is a usage right, not a content change. A withdrawn node keeps its
body, so an audit read still returns the exact reference that was registered,
while list, search, and Context stop offering the node until it is restored.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, ValidationInfo, field_validator
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef
from powercontext.artifacts.models import MemoryCitation
from powercontext.builtin.artifacts.memory import MemoryService
from powercontext.builtin.artifacts.memory.errors import InvalidMemoryCitationError, MemoryEntryNotFoundError
from powercontext.builtin.artifacts.memory.models import MemoryEntryVersion
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.errors import RepositoryNotFoundError
from powercontext.builtin.persistence.source_lifecycle import (
    SourceLifecycleFamily,
    SourceLifecycleNode,
    SourceLifecyclePolicy,
    SourceLifecycleRepository,
)
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.persistence.topic_memory import TopicMemoryRepository
from powercontext.errors import ArtifactNotFoundError
from powercontext.limits import MAX_SOURCE_LIFECYCLE_NODE_ID_LENGTH
from powercontext.sources.models import SourceRef

SOURCE_LIFECYCLE_TOPIC_FAMILY = "topic-memory"
SOURCE_LIFECYCLE_PURPOSES = ("generation", "audit")
_LIFECYCLE_REASON_PREFIX = "source-lifecycle"
_MEMORY_REASON_LIMIT = 512


class SourceLifecycleError(ValueError):
    """Report one rejected Source lifecycle operation."""

    def __init__(self, code: str) -> None:
        self.code = code
        messages = {
            "unknown-node": "the requested node is not registered in this scope",
            "unknown-source": "the event does not name a registered Source",
            "unknown-purpose": "the requested purpose is not a known lifecycle purpose",
            "node-unavailable": "the requested node has no current support",
            "revision-mismatch": "the event revision does not match the Source lifecycle version",
            "sequence-regression": "the event sequence is older than the applied sequence",
            "sequence-conflict": "the event sequence was already applied with different content",
            "registration-conflict": "the node id is already registered with a different binding",
            "unknown-parent": "a parent node is not registered in this scope",
            "duplicate-parent": "a parent node is listed more than once",
            "self-parent": "a node cannot depend on itself",
            "empty-parents": "a derived node requires at least one parent",
            "invalid-policy": "the dependency policy must be all or any",
            "invalid-node-id": "the node id must be a non-blank string",
            "invalid-revision": "the lifecycle revision must be a positive integer",
            "invalid-reference": "the registered reference does not match the node family",
            "unreadable-reference": "the registered reference does not exist or cannot be read",
            "dependency-cycle": "the dependency graph contains a cycle",
            "invalid-event": "the applied event is not a Source lifecycle event",
            "unavailable": "this runtime does not provide the Source lifecycle service",
        }
        super().__init__(messages.get(code, f"invalid Source lifecycle request: {code}"))


class SourceLifecycleEvent(BaseModel):
    """One validated Source lifecycle transition."""

    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(min_length=1)
    revision: StrictInt = Field(gt=0)
    sequence: StrictInt = Field(gt=0)
    available: StrictBool
    reason: str = Field(min_length=1)

    @field_validator("node_id", "reason")
    @classmethod
    def _require_visible_text(cls, value: str, info: ValidationInfo) -> str:
        if not value.strip():
            raise ValueError(f"{info.field_name} must not be blank")  # noqa: TRY003
        return value


@dataclass(frozen=True, slots=True)
class SourceLifecycleGraph:
    """One scope's registered nodes and their computed availability."""

    nodes: Mapping[str, SourceLifecycleNode]
    available: Mapping[str, bool]

    def available_node_ids(self) -> list[str]:
        """Return every available node id in node id order."""

        return sorted(node_id for node_id, usable in self.available.items() if usable)


class SourceLifecycleService:
    """Operate one scope's evidence dependency graph and availability propagation."""

    def __init__(
        self,
        *,
        database: AsyncDatabase,
        scope_id: str,
        memory: Callable[[AsyncConnection | None], MemoryService],
        memory_artifact_id: str,
        sources: SourceRepository,
        artifacts: ArtifactRepository,
        lifecycle: SourceLifecycleRepository,
        topic_memories: TopicMemoryRepository,
    ) -> None:
        self._database = database
        self._scope_id = scope_id
        self._memory = memory
        self._memory_artifact_id = memory_artifact_id
        self._sources = sources
        self._artifacts = artifacts
        self._lifecycle = lifecycle
        self._topic_memories = topic_memories

    async def register_source(
        self,
        node_id: str,
        source_ref: SourceRef,
        revision: int = 1,
    ) -> None:
        """Register one Source as the root of this scope's evidence graph."""

        identity = _require_node_id(node_id)
        version = _require_revision(revision)
        if not isinstance(source_ref, SourceRef):
            raise SourceLifecycleError("invalid-reference")
        async with self._database.transaction() as connection:
            graph = await self._load(connection)
            existing = graph.nodes.get(identity)
            if existing is not None:
                _require_same_binding(existing, "source", source_ref, (), "all", version)
                return
            await self._require_source(connection, source_ref)
            await self._lifecycle.insert(
                connection,
                self._scope_id,
                SourceLifecycleNode(
                    node_id=identity,
                    family="source",
                    reference=source_ref,
                    revision=version,
                    available=True,
                ),
            )

    async def register_memory(
        self,
        node_id: str,
        citation: MemoryCitation,
        parents: Sequence[str] = (),
        policy: str = "all",
    ) -> None:
        """Register one Memory entry with the parents that support its body."""

        identity = _require_node_id(node_id)
        if not isinstance(citation, MemoryCitation):
            raise SourceLifecycleError("invalid-reference")
        bindings = _require_parents(identity, parents)
        rule = _require_policy(policy)
        await self._register_derived(identity, "memory", citation, bindings, rule)

    async def register_topic(
        self,
        node_id: str,
        artifact_ref: ArtifactRef,
        parents: Sequence[str] = (),
        policy: str = "all",
    ) -> None:
        """Register one Topic Memory artifact with the parents that support it."""

        identity = _require_node_id(node_id)
        if not isinstance(artifact_ref, ArtifactRef) or artifact_ref.family != SOURCE_LIFECYCLE_TOPIC_FAMILY:
            raise SourceLifecycleError("invalid-reference")
        bindings = _require_parents(identity, parents)
        rule = _require_policy(policy)
        await self._register_derived(identity, "topic", artifact_ref, bindings, rule)

    async def apply(self, event: SourceLifecycleEvent) -> list[str]:
        """Apply one Source lifecycle event and propagate availability."""

        if not isinstance(event, SourceLifecycleEvent):
            raise SourceLifecycleError("invalid-event")
        digest = _event_digest(event)
        async with self._database.transaction() as connection:
            graph = await self._load(connection)
            node = graph.nodes.get(event.node_id)
            if node is None or node.family != "source":
                raise SourceLifecycleError("unknown-source")
            if node.revision != event.revision:
                raise SourceLifecycleError("revision-mismatch")
            if event.sequence < node.sequence:
                raise SourceLifecycleError("sequence-regression")
            if event.sequence == node.sequence and node.digest != digest:
                raise SourceLifecycleError("sequence-conflict")
            updated = node.model_copy(
                update={
                    "available": event.available,
                    "sequence": event.sequence,
                    "digest": digest,
                }
            )
            nodes = {**graph.nodes, event.node_id: updated}
            available = _compute_availability(nodes)
            # The native Memory state joins this transaction, so an interrupted
            # propagation leaves the recorded event to be completed by its retry.
            await self._propagate_memory(connection, nodes, available, event.reason)
            await self._lifecycle.set_source_state(
                connection,
                self._scope_id,
                event.node_id,
                available=event.available,
                sequence=event.sequence,
                digest=digest,
            )
            await self._write_topic_eligibility(connection, nodes, available)
        return SourceLifecycleGraph(nodes=nodes, available=available).available_node_ids()

    async def resolve(
        self,
        node_id: str,
        purpose: str = "generation",
    ) -> SourceRef | MemoryCitation | ArtifactRef:
        """Return the exact reference of one node for the requested purpose."""

        if purpose not in SOURCE_LIFECYCLE_PURPOSES:
            raise SourceLifecycleError("unknown-purpose")
        async with self._database.transaction() as connection:
            graph = await self._load(connection)
        node = graph.nodes.get(node_id)
        if node is None:
            raise SourceLifecycleError("unknown-node")
        if purpose == "generation" and not graph.available.get(node_id, False):
            raise SourceLifecycleError("node-unavailable")
        return node.reference

    async def _register_derived(
        self,
        identity: str,
        family: SourceLifecycleFamily,
        reference: MemoryCitation | ArtifactRef,
        parents: tuple[str, ...],
        policy: SourceLifecyclePolicy,
    ) -> None:
        async with self._database.transaction() as connection:
            graph = await self._load(connection)
            existing = graph.nodes.get(identity)
            if existing is not None:
                _require_same_binding(existing, family, reference, parents, policy, existing.revision)
                return
            for parent in parents:
                if parent not in graph.nodes:
                    raise SourceLifecycleError("unknown-parent")
            if family == "memory":
                await self._require_memory_citation(connection, reference)
            else:
                await self._require_artifact(connection, reference)
            node = SourceLifecycleNode(
                node_id=identity,
                family=family,
                reference=reference,
                parents=parents,
                policy=policy,
            )
            nodes = {**graph.nodes, identity: node}
            available = _compute_availability(nodes)
            await self._propagate_memory(connection, nodes, available, None)
            await self._lifecycle.insert(connection, self._scope_id, node)
            await self._write_topic_eligibility(connection, nodes, available)

    async def _load(self, connection: AsyncConnection) -> SourceLifecycleGraph:
        nodes = {node.node_id: node for node in await self._lifecycle.nodes(connection, self._scope_id)}
        return SourceLifecycleGraph(nodes=nodes, available=_compute_availability(nodes))

    async def _require_source(self, connection: AsyncConnection, source_ref: SourceRef) -> None:
        try:
            await self._sources.get(connection, self._scope_id, source_ref)
        except RepositoryNotFoundError as error:
            raise SourceLifecycleError("unreadable-reference") from error

    async def _require_artifact(self, connection: AsyncConnection, artifact_ref: ArtifactRef) -> None:
        try:
            await self._artifacts.get(connection, self._scope_id, artifact_ref)
        except RepositoryNotFoundError as error:
            raise SourceLifecycleError("unreadable-reference") from error

    async def _require_memory_citation(
        self,
        connection: AsyncConnection,
        citation: MemoryCitation,
    ) -> None:
        if citation.memory_ref.artifact_id != self._memory_artifact_id:
            raise SourceLifecycleError("invalid-reference")
        try:
            await self._memory(connection).validate_citation(citation)
        except (ArtifactNotFoundError, InvalidMemoryCitationError, MemoryEntryNotFoundError) as error:
            raise SourceLifecycleError("unreadable-reference") from error

    async def _write_topic_eligibility(
        self,
        connection: AsyncConnection,
        nodes: Mapping[str, SourceLifecycleNode],
        available: Mapping[str, bool],
    ) -> None:
        for node_id, node in nodes.items():
            if node.family != "topic":
                continue
            artifact_ref = node.reference
            await self._topic_memories.set_eligibility(
                connection,
                self._scope_id,
                artifact_ref.artifact_id,
                eligible=available.get(node_id, False),
                revision=artifact_ref.revision,
            )

    async def _propagate_memory(
        self,
        connection: AsyncConnection,
        nodes: Mapping[str, SourceLifecycleNode],
        available: Mapping[str, bool],
        reason: str | None,
    ) -> None:
        """Align the native Memory entries with the computed availability."""

        entries = [node for node in nodes.values() if node.family == "memory"]
        if not entries:
            return
        memory = self._memory(connection)
        try:
            head = await memory.head(self._memory_artifact_id)
        except ArtifactNotFoundError:
            return
        current = {entry.entry_id: entry for entry in await memory.entries(head)}
        states = {item.entry_id: item.state for item in head.content.manifest.entries}
        deactivate: list[MemoryEntryVersion] = []
        reactivate: list[MemoryEntryVersion] = []
        for node in entries:
            citation = node.reference
            entry = current.get(citation.entry_id)
            if entry is None:
                continue
            state = states.get(entry.entry_id)
            if available.get(node.node_id, False) and state == "inactive":
                reactivate.append(entry)
            elif not available.get(node.node_id, False) and state == "active":
                deactivate.append(entry)
        audit_reason = _memory_reason(reason)
        if deactivate:
            await memory.forget(head, entries=tuple(deactivate), reason=audit_reason)
        if reactivate:
            restored = await memory.head(self._memory_artifact_id)
            await memory.reactivate(restored, entries=tuple(reactivate), reason=audit_reason)


def _require_node_id(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SourceLifecycleError("invalid-node-id")
    if len(value) > MAX_SOURCE_LIFECYCLE_NODE_ID_LENGTH:
        raise SourceLifecycleError("invalid-node-id")
    return value


def _require_revision(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise SourceLifecycleError("invalid-revision")
    return value


def _require_policy(value: object) -> SourceLifecyclePolicy:
    if value not in {"all", "any"}:
        raise SourceLifecycleError("invalid-policy")
    return value


def _require_parents(node_id: str, parents: Sequence[str]) -> tuple[str, ...]:
    if isinstance(parents, str) or not isinstance(parents, Sequence) or not parents:
        raise SourceLifecycleError("empty-parents")
    bindings: list[str] = []
    for parent in parents:
        if not isinstance(parent, str) or not parent.strip():
            raise SourceLifecycleError("unknown-parent")
        if parent == node_id:
            raise SourceLifecycleError("self-parent")
        if parent in bindings:
            raise SourceLifecycleError("duplicate-parent")
        bindings.append(parent)
    return tuple(bindings)


def _require_same_binding(
    existing: SourceLifecycleNode,
    family: SourceLifecycleFamily,
    reference: SourceRef | MemoryCitation | ArtifactRef,
    parents: tuple[str, ...],
    policy: SourceLifecyclePolicy,
    revision: int,
) -> None:
    if (
        existing.family != family
        or existing.reference != reference
        or existing.parents != parents
        or existing.policy != policy
        or existing.revision != revision
    ):
        raise SourceLifecycleError("registration-conflict")


def _compute_availability(nodes: Mapping[str, SourceLifecycleNode]) -> dict[str, bool]:
    """Resolve every node's availability from its parents in dependency order."""

    available: dict[str, bool] = {}
    pending = dict(nodes)
    while pending:
        progressed = False
        for node_id, node in list(pending.items()):
            if not all(parent in available for parent in node.parents):
                continue
            available[node_id] = _supported(node, available)
            del pending[node_id]
            progressed = True
        if not progressed:
            raise SourceLifecycleError("dependency-cycle")
    return available


def _supported(node: SourceLifecycleNode, available: Mapping[str, bool]) -> bool:
    if node.family == "source":
        return node.available
    support = [available[parent] for parent in node.parents]
    if node.policy == "all":
        return all(support)
    return any(support)


def _event_digest(event: SourceLifecycleEvent) -> str:
    """Return the identity of one applied event, so a replay stays idempotent."""

    payload = json.dumps(
        {
            "node_id": event.node_id,
            "revision": event.revision,
            "sequence": event.sequence,
            "available": event.available,
            "reason": event.reason,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _memory_reason(reason: str | None) -> str:
    if reason is None:
        return _LIFECYCLE_REASON_PREFIX
    return f"{_LIFECYCLE_REASON_PREFIX}: {reason}"[:_MEMORY_REASON_LIMIT]


__all__ = [
    "SOURCE_LIFECYCLE_PURPOSES",
    "SOURCE_LIFECYCLE_TOPIC_FAMILY",
    "SourceLifecycleError",
    "SourceLifecycleEvent",
    "SourceLifecycleGraph",
    "SourceLifecycleService",
]
