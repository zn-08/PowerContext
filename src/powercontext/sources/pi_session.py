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

"""Pi session branch capture so one raw event stays addressable as one Source."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from pydantic import BaseModel, JsonValue

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.connectors import (
    ConnectorRunCompletion,
    ConnectorRunSession,
    ConnectorRunStatus,
    ConnectorSubmissionStatus,
)
from powercontext.sources.definitions import AdapterSourceDefinition, SourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

PI_EVENT_SOURCE_NAME = "pi-event"
PI_EVENT_SOURCE_VERSION = "1"
PI_EVENT_SOURCE_ID_PREFIX = "pi:"
PI_SESSION_CONNECTOR_NAME = "pi-session-history"
PI_SESSION_CONNECTOR_VERSION = "1"

_PI_CHECKPOINT_VERSION = 1
_PI_SESSION_VERSION = 3
_PI_SUMMARY_TYPES = frozenset({"compaction", "branch_summary"})


class PiSessionInput(BaseModel):
    """One offline Pi session archive and the selected branch leaf."""

    content: bytes
    leaf_id: str | None = None


class PiEventInput(BaseModel):
    """One visible Pi event captured from the selected branch."""

    session_id: str
    event_id: str
    parent_id: str | None = None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


class PiEventSource(Source):
    """One captured Pi event that a citation can resolve back to its record."""

    source_id: str
    session_id: str
    event_id: str
    parent_id: str | None = None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


class PiEventSourceAdapter:
    """Resolve and read the event-level Source owned by this Definition."""

    input_class = PiEventInput
    name = PI_EVENT_SOURCE_NAME
    source_class = PiEventSource

    async def resolve(self, value: PiEventInput, /) -> PiEventSource:
        source_id = pi_event_source_id(value.session_id, value.event_id)
        return PiEventSource(
            name=source_id,
            source_id=source_id,
            materialization=SourceMaterialization.CAPTURED,
            session_id=value.session_id,
            event_id=value.event_id,
            parent_id=value.parent_id,
            event_type=value.event_type,
            role=value.role,
            content=value.content,
            record=value.record,
        )

    async def read(self, source: PiEventSource, /) -> PiEventInput:
        return PiEventInput(
            session_id=source.session_id,
            event_id=source.event_id,
            parent_id=source.parent_id,
            event_type=source.event_type,
            role=source.role,
            content=source.content,
            record=source.record,
        )


class PiEventTextEvidenceProjection:
    """Expose one captured Pi event as the standard textual evidence shape."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PiEventSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PiEventSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PI_EVENT_SOURCE_NAME,
            source_id=source.source_id,
            content=source.content,
            metadata={
                "session_id": source.session_id,
                "event_id": source.event_id,
                "parent_id": source.parent_id,
                "event_type": source.event_type,
                "role": source.role,
            },
        )


PI_EVENT_SOURCE_DEFINITION: SourceDefinition[PiEventInput, PiEventSource, PiEventInput] = AdapterSourceDefinition(
    PiEventSourceAdapter(),
    version=PI_EVENT_SOURCE_VERSION,
    projections=(PiEventTextEvidenceProjection(),),
)


class PiSessionConnector:
    """Import the visible events of one Pi branch without repeating past work."""

    name = PI_SESSION_CONNECTOR_NAME
    version = PI_SESSION_CONNECTOR_VERSION
    source_definitions = frozenset({PI_EVENT_SOURCE_NAME})

    def __init__(self, path: str | Path, leaf_id: str | None = None) -> None:
        self.path = Path(path)
        self.leaf_id = leaf_id

    async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
        """Capture the events of the selected branch that are not imported yet."""

        archive = _parse_archive(self.path.read_bytes())
        events = _select_branch(archive, self.leaf_id)
        imported = _imported_events(session.checkpoint)
        known = imported.get(archive.session_id, {})
        pending = _pending_events(events, known)
        _reject_rewritten_history(archive, known, {event.event_id for event in events})

        accepted: list[PiEventInput] = []
        for event in pending:
            outcome = await session.submit(
                pi_event_source_id(event.session_id, event.event_id),
                PI_EVENT_SOURCE_NAME,
                event,
            )
            if outcome.status is ConnectorSubmissionStatus.ACCEPTED:
                accepted.append(event)
        return ConnectorRunCompletion(
            status=ConnectorRunStatus.COMPLETE,
            checkpoint=_checkpoint_with_events(
                session.checkpoint,
                archive.session_id,
                {event.event_id: _record_digest(event.record) for event in accepted},
            ),
        )


def pi_event_source_id(session_id: str, event_id: str) -> str:
    """Return the stable identity of one event inside one Pi session."""

    _validate_identity_part("session id", session_id)
    _validate_identity_part("event id", event_id)
    identity = f"{PI_EVENT_SOURCE_ID_PREFIX}{quote(session_id, safe='')}:{quote(event_id, safe='')}"
    if len(identity) <= MAX_SOURCE_ID_LENGTH:
        return identity
    digest = hashlib.sha256(f"{session_id}\x00{event_id}".encode()).hexdigest()
    return f"{PI_EVENT_SOURCE_ID_PREFIX}digest:{digest}"


def parse_pi_session(value: PiSessionInput) -> tuple[PiEventInput, ...]:
    """Parse one Pi session archive into the visible events of the selected branch."""

    archive = _parse_archive(value.content)
    return _select_branch(archive, value.leaf_id)


@dataclass(frozen=True, slots=True)
class _PiArchive:
    """One parsed Pi session archive and the node graph it stores."""

    session_id: str
    records: Mapping[str, dict[str, JsonValue]]
    parents: Mapping[str, str | None]
    order: tuple[str, ...]


def _parse_archive(content: bytes) -> _PiArchive:
    """Decode one archive into its node graph without accepting partial results."""

    records = _decode_records(_decode_archive(content))
    session_id = _session_header_id(records[0])
    nodes: dict[str, dict[str, JsonValue]] = {}
    parents: dict[str, str | None] = {}
    order: list[str] = []
    for record in records[1:]:
        node_id = _node_id(record)
        if node_id in nodes:
            raise ValueError(f"Pi session node id {node_id!r} appears more than once")  # noqa: TRY003
        parents[node_id] = _parent_id(record, nodes)
        nodes[node_id] = record
        order.append(node_id)
    return _PiArchive(session_id=session_id, records=nodes, parents=parents, order=tuple(order))


def _decode_archive(content: bytes) -> str:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("Pi session content must be valid UTF-8") from error  # noqa: TRY003
    return text.removeprefix("\ufeff")


def _decode_records(text: str) -> list[dict[str, JsonValue]]:
    records: list[dict[str, JsonValue]] = []
    for line in re.split(r"\r\n|\r|\n", text):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError as error:
            raise ValueError(f"Pi session line is not valid JSON: {error}") from error  # noqa: TRY003
        if not isinstance(record, dict):
            raise ValueError("Pi session line must be a JSON object")  # noqa: TRY003, TRY004
        records.append(record)
    if not records:
        raise ValueError("Pi session archive must contain a session header")  # noqa: TRY003
    return records


def _session_header_id(header: dict[str, JsonValue]) -> str:
    if header.get("type") != "session":
        raise ValueError("Pi session archive must start with a session header")  # noqa: TRY003
    if header.get("version") != _PI_SESSION_VERSION:
        raise ValueError("Pi session header must declare version 3")  # noqa: TRY003
    session_id = header.get("id")
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("Pi session header must declare a non-empty id")  # noqa: TRY003
    return session_id


def _node_id(record: dict[str, JsonValue]) -> str:
    node_id = record.get("id")
    if not isinstance(node_id, str) or not node_id:
        raise ValueError("Pi session node must declare a non-empty id")  # noqa: TRY003
    return node_id


def _parent_id(record: dict[str, JsonValue], seen: Mapping[str, dict[str, JsonValue]]) -> str | None:
    parent = record.get("parentId")
    if parent is None:
        return None
    if not isinstance(parent, str):
        raise ValueError("Pi session parentId must be a string or null")  # noqa: TRY003, TRY004
    if not parent:
        return None
    if parent not in seen:
        raise ValueError(f"Pi session parent {parent!r} does not appear before its child")  # noqa: TRY003
    return parent


def _select_branch(archive: _PiArchive, leaf_id: str | None) -> tuple[PiEventInput, ...]:
    """Return the visible events of the selected branch, ordered from root to leaf."""

    if not archive.order:
        raise ValueError("Pi session archive must contain at least one node")  # noqa: TRY003
    leaf = archive.order[-1] if leaf_id is None else leaf_id
    if leaf not in archive.records:
        raise ValueError(f"Pi session leaf {leaf!r} does not exist")  # noqa: TRY003
    events = []
    for node_id in _ancestor_chain(archive, leaf):
        record = archive.records[node_id]
        rendered = _render_event(record)
        if rendered is None:
            continue
        role, body = rendered
        events.append(
            PiEventInput(
                session_id=archive.session_id,
                event_id=node_id,
                parent_id=archive.parents[node_id],
                event_type=_text(record.get("type")),
                role=role,
                content=f"{role}: {body}",
                record=record,
            )
        )
    return tuple(events)


def _ancestor_chain(archive: _PiArchive, leaf: str) -> list[str]:
    """Walk from one leaf to its root, refusing a cyclic history."""

    chain: list[str] = []
    visited: set[str] = set()
    current: str | None = leaf
    while current is not None:
        if current in visited:
            raise ValueError("Pi session history contains a cycle")  # noqa: TRY003
        visited.add(current)
        chain.append(current)
        current = archive.parents[current]
    chain.reverse()
    return chain


def _render_event(record: dict[str, JsonValue]) -> tuple[str, str] | None:
    """Return the visible role and body of one record, or None when it carries none."""

    event_type = record.get("type")
    if not isinstance(event_type, str):
        return None
    if event_type == "message":
        return _render_message(record.get("message"))
    if event_type == "custom_message":
        body = _content_body(record.get("content"))
        return ("custom", body) if body else None
    if event_type in _PI_SUMMARY_TYPES:
        summary = record.get("summary")
        body = summary if isinstance(summary, str) else ""
        return (event_type, body) if body else None
    return None


def _render_message(message: object) -> tuple[str, str] | None:
    """Return the visible role and body of one message entry."""

    if not isinstance(message, dict):
        return None
    role = message.get("role")
    if not isinstance(role, str) or not role:
        return None
    if role == "toolResult":
        marker = f"[tool_result {_text(message.get('toolName'))} {_text(message.get('toolCallId'))}]"
        text = _text_body(message.get("content"))
        return (role, f"{marker} {text}" if text else marker)
    if role == "bashExecution":
        command = _text(message.get("command"))
        output = _text(message.get("output"))
        lines = [f"$ {command}"] if command else []
        if output:
            lines.append(output)
        return (role, "\n".join(lines)) if lines else None
    body = _content_body(message.get("content"))
    return (role, body) if body else None


def _content_body(content: object) -> str:
    """Return the visible body of one message content value."""

    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(body for body in (_block_body(block) for block in content) if body)


def _text_body(content: object) -> str:
    """Return the visible text of one message content value."""

    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(text for text in (_block_text(block) for block in content) if text)


def _block_body(block: object) -> str:
    """Return the visible text of one content block."""

    if not isinstance(block, dict):
        return ""
    block_type = block.get("type")
    if block_type == "text":
        return _text(block.get("text"))
    if block_type == "toolCall":
        return _tool_call_body(block)
    return ""


def _block_text(block: object) -> str:
    """Return the text of one text block."""

    if not isinstance(block, dict) or block.get("type") != "text":
        return ""
    return _text(block.get("text"))


def _tool_call_body(block: Mapping[str, JsonValue]) -> str:
    """Return one tool call as its compact, key-sorted argument payload."""

    name = _text(block.get("name"))
    call_id = _text(block.get("id"))
    return f"[tool_call {name} {call_id}] {_compact_json(block.get('arguments'))}"


def _compact_json(value: object) -> str:
    """Render JSON arguments without extra whitespace and with sorted keys."""

    if value is None:
        return "{}"
    for sort_keys in (True, False):
        try:
            return json.dumps(value, ensure_ascii=False, sort_keys=sort_keys, separators=(",", ":"))
        except (TypeError, ValueError):
            continue
    return "{}"


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _record_digest(record: Mapping[str, JsonValue]) -> str:
    """Return the checksum of one original record used to detect rewrites."""

    encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _imported_events(checkpoint: JsonValue | None) -> dict[str, dict[str, str]]:
    """Return the event identities and record checksums already imported."""

    if not isinstance(checkpoint, dict):
        return {}
    imported = checkpoint.get("imported")
    if not isinstance(imported, dict):
        return {}
    sessions: dict[str, dict[str, str]] = {}
    for session_id, events in imported.items():
        if not isinstance(session_id, str) or not isinstance(events, dict):
            continue
        sessions[session_id] = {
            event_id: digest
            for event_id, digest in events.items()
            if isinstance(event_id, str) and isinstance(digest, str)
        }
    return sessions


def _pending_events(events: tuple[PiEventInput, ...], known: Mapping[str, str]) -> list[PiEventInput]:
    """Return the branch events that are not imported yet, refusing rewritten ones."""

    pending: list[PiEventInput] = []
    for event in events:
        digest = _record_digest(event.record)
        previous = known.get(event.event_id)
        if previous is None:
            pending.append(event)
        elif previous != digest:
            raise ValueError(f"Pi session event {event.event_id!r} was rewritten after it was imported")  # noqa: TRY003
    return pending


def _reject_rewritten_history(
    archive: _PiArchive,
    known: Mapping[str, str],
    branch_ids: set[str],
) -> None:
    """Refuse a file whose already imported events were rewritten off the branch."""

    for event_id, digest in known.items():
        if event_id in branch_ids:
            continue
        record = archive.records.get(event_id)
        if record is None:
            continue
        if _record_digest(record) != digest:
            raise ValueError(f"Pi session event {event_id!r} was rewritten after it was imported")  # noqa: TRY003


def _checkpoint_with_events(
    checkpoint: JsonValue | None,
    session_id: str,
    events: Mapping[str, str],
) -> JsonValue:
    """Return the checkpoint that records the imported events of one session."""

    base = dict(checkpoint) if isinstance(checkpoint, dict) else {}
    imported = base.get("imported")
    sessions = dict(imported) if isinstance(imported, dict) else {}
    current = sessions.get(session_id)
    session_events = dict(current) if isinstance(current, dict) else {}
    session_events.update(events)
    sessions[session_id] = session_events
    base["version"] = _PI_CHECKPOINT_VERSION
    base["imported"] = sessions
    return base


def _validate_identity_part(field: str, value: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"Pi {field} must be a non-empty string")  # noqa: TRY003


__all__ = [
    "PI_EVENT_SOURCE_DEFINITION",
    "PI_EVENT_SOURCE_ID_PREFIX",
    "PI_EVENT_SOURCE_NAME",
    "PI_EVENT_SOURCE_VERSION",
    "PI_SESSION_CONNECTOR_NAME",
    "PI_SESSION_CONNECTOR_VERSION",
    "PiEventInput",
    "PiEventSource",
    "PiEventSourceAdapter",
    "PiEventTextEvidenceProjection",
    "PiSessionConnector",
    "PiSessionInput",
    "parse_pi_session",
    "pi_event_source_id",
]
