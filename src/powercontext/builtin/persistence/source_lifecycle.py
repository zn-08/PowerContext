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

"""Persist one scope's evidence dependency graph and Source lifecycle state."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef
from powercontext.artifacts.models import MemoryCitation
from powercontext.builtin.persistence.codec import dump_model, load_model
from powercontext.builtin.persistence.errors import InvalidStoredPayloadError
from powercontext.builtin.persistence.tables import SOURCE_LIFECYCLE_NODES_TABLE
from powercontext.sources.models import SourceRef

SourceLifecycleFamily = Literal["source", "memory", "topic"]
SourceLifecyclePolicy = Literal["all", "any"]

# The node family selects the reference model, so one payload column stores
# every family without a lossy common shape.
_REFERENCE_MODELS: dict[str, type[BaseModel]] = {
    "source": SourceRef,
    "memory": MemoryCitation,
    "topic": ArtifactRef,
}


class SourceLifecycleNode(BaseModel):
    """One registered node of one scope's evidence dependency graph."""

    model_config = ConfigDict(frozen=True)

    node_id: str
    family: SourceLifecycleFamily
    reference: SourceRef | MemoryCitation | ArtifactRef
    parents: tuple[str, ...] = ()
    policy: SourceLifecyclePolicy = "all"
    revision: int = Field(default=0, ge=0)
    sequence: int = Field(default=0, ge=0)
    available: bool = True
    digest: str | None = None


class SourceLifecycleRepository:
    """Store the registered nodes and Source states of one scope."""

    async def nodes(
        self,
        connection: AsyncConnection,
        scope_id: str,
        /,
    ) -> tuple[SourceLifecycleNode, ...]:
        """Return every registered node of one scope in node id order."""

        rows = (
            await connection.execute(
                select(SOURCE_LIFECYCLE_NODES_TABLE).where(
                    SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == scope_id,
                )
            )
        ).mappings()
        return tuple(_decode_node(row) for row in rows)

    async def insert(
        self,
        connection: AsyncConnection,
        scope_id: str,
        node: SourceLifecycleNode,
        /,
    ) -> None:
        """Register one node of one scope."""

        await connection.execute(insert(SOURCE_LIFECYCLE_NODES_TABLE).values(_node_values(scope_id, node)))

    async def set_source_state(
        self,
        connection: AsyncConnection,
        scope_id: str,
        node_id: str,
        *,
        available: bool,
        sequence: int,
        digest: str,
    ) -> None:
        """Record the lifecycle state of one registered Source."""

        await connection.execute(
            update(SOURCE_LIFECYCLE_NODES_TABLE)
            .where(
                SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == scope_id,
                SOURCE_LIFECYCLE_NODES_TABLE.c.node_id == node_id,
            )
            .values(available=available, sequence=sequence, digest=digest)
        )


def _node_values(scope_id: str, node: SourceLifecycleNode) -> dict[str, object]:
    return {
        "scope_id": scope_id,
        "node_id": node.node_id,
        "family": node.family,
        "reference": dump_model(node.reference, kind="source-lifecycle", name="reference"),
        "parents": json.dumps(list(node.parents), ensure_ascii=False, separators=(",", ":")).encode(),
        "policy": node.policy,
        "revision": node.revision,
        "sequence": node.sequence,
        "available": node.available,
        "digest": node.digest,
    }


def _decode_node(row: Mapping[Any, Any]) -> SourceLifecycleNode:
    family = str(row["family"])
    model = _REFERENCE_MODELS.get(family)
    if model is None:
        raise InvalidStoredPayloadError("source-lifecycle", "reference", "unknown node family")
    reference = load_model(model, row["reference"], kind="source-lifecycle", name="reference")
    parents = tuple(json.loads(bytes(row["parents"]).decode()))
    return SourceLifecycleNode(
        node_id=str(row["node_id"]),
        family=cast(SourceLifecycleFamily, family),
        reference=cast("SourceRef | MemoryCitation | ArtifactRef", reference),
        parents=parents,
        policy=cast(SourceLifecyclePolicy, str(row["policy"])),
        revision=int(row["revision"]),
        sequence=int(row["sequence"]),
        available=bool(row["available"]),
        digest=None if row["digest"] is None else str(row["digest"]),
    )


__all__ = [
    "SourceLifecycleFamily",
    "SourceLifecycleNode",
    "SourceLifecyclePolicy",
    "SourceLifecycleRepository",
]
