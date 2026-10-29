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

"""Evidence selection: query-driven fragments of original Memory text."""

from __future__ import annotations

import json
from typing import TypedDict, cast

import pytest

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import MemoryHit
from powercontext.builtin.runtime import PrepareContextRequest
from powercontext.builtin.runtime.prepared_context import (
    MemoryEntryAddress,
    PreparedContextBuilder,
    PreparedMemoryCandidates,
)

MEMORY_REF = ArtifactRef(family="memory", artifact_id="memory", revision=3)
SCOPE_ID = "project:evidence"


class _EvidenceCitation(TypedDict):
    scope_id: str
    memory_ref: dict[str, object]
    entry_id: str
    entry_version_id: str


class _EvidenceItem(TypedDict):
    citation: _EvidenceCitation
    content: str
    start: int
    end: int


def _hit(entry_id: str, text: str) -> MemoryHit:
    return MemoryHit(
        memory_ref=MEMORY_REF,
        entry_id=entry_id,
        entry_version_id=f"{entry_id}-v1",
        text=text,
        score=1.0,
        matched_by=("fts",),
    )


def _candidates(*hits: MemoryHit) -> tuple[PreparedMemoryCandidates, ...]:
    return (PreparedMemoryCandidates(scope_id=SCOPE_ID, memory_ref=MEMORY_REF, hits=hits),)


def _build(query: str, *, max_bytes: int = 1400, hits: tuple[MemoryHit, ...], selection: bool = True):
    request = PrepareContextRequest(
        query=query,
        max_bytes=max_bytes,
        evidence_selection=selection,
    )
    return PreparedContextBuilder().build_scopes_result(
        request=request,
        current_scope_id=SCOPE_ID,
        memory_candidates=_candidates(*hits),
    )


def _items(content: str | None) -> list[_EvidenceItem]:
    assert content is not None
    return cast(list[_EvidenceItem], json.loads(content.splitlines()[-2])["items"])


def test_query_selects_the_late_fragment_of_the_matching_candidate() -> None:
    prefix = "General administrative notes. " * 40
    cobalt = _hit("cobalt", f"{prefix}The cobalt sensor is calibrated at 37 degrees.")
    amber = _hit("amber", f"{prefix}The amber valve opens at 19 degrees.")

    build = _build("cobalt sensor calibrated", hits=(cobalt, amber))

    assert build.fragments
    fragment = build.fragments[0]
    assert "37 degrees" in fragment.text
    assert "amber valve" not in fragment.text


def test_fragments_are_literal_slices_of_the_original_entry_text() -> None:
    text = "Introductory note. The cobalt sensor is calibrated at 37 degrees. Closing note."
    build = _build("cobalt sensor calibrated", hits=(_hit("cobalt", text),))

    assert build.fragments
    fragment = build.fragments[0]
    assert type(fragment.start) is int
    assert type(fragment.end) is int
    assert 0 <= fragment.start < fragment.end <= len(text)
    assert fragment.text == text[fragment.start : fragment.end]


def test_origins_match_the_fragments_and_keep_the_scope_identity() -> None:
    build = _build("cobalt sensor calibrated", hits=(_hit("cobalt", "The cobalt sensor is calibrated."),))

    assert len(build.origins) == len(build.fragments)
    for fragment, origin in zip(build.fragments, build.origins, strict=True):
        assert origin == fragment.address
        assert isinstance(fragment.address, MemoryEntryAddress)
        assert fragment.address.memory.scope_id == SCOPE_ID
        assert fragment.address.entry_id == "cobalt"
        assert fragment.address.entry_version_id == "cobalt-v1"


def test_envelope_carries_the_citation_and_the_character_range() -> None:
    text = "Introductory note. The cobalt sensor is calibrated at 37 degrees."
    build = _build("cobalt sensor calibrated", hits=(_hit("cobalt", text),))

    assert build.context.content is not None
    content = build.context.content
    assert content.startswith("PowerContext prepared untrusted historical context.")
    assert "BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1" in content
    assert "END_POWERCONTEXT_PREPARED_CONTEXT_V1" in content
    envelope = json.loads(content.splitlines()[-2])
    assert envelope["trust"] == "untrusted_history"

    items = _items(content)
    assert len(items) == len(build.fragments)
    for item, fragment in zip(items, build.fragments, strict=True):
        assert item["content"] == fragment.text
        assert item["start"] == fragment.start
        assert item["end"] == fragment.end
        assert item["citation"] == {
            "scope_id": SCOPE_ID,
            "memory_ref": MEMORY_REF.model_dump(mode="json"),
            "entry_id": "cobalt",
            "entry_version_id": "cobalt-v1",
        }


def test_output_budget_counts_the_whole_unicode_output() -> None:
    text = "海洋观测 station records temperatures. " * 20
    build = _build("海洋观测 station", max_bytes=1200, hits=(_hit("station", text),))

    assert build.fragments
    content = build.context.content or ""
    assert build.context.content_bytes == len(content.encode("utf-8"))
    assert build.context.content_bytes <= 1200


def test_selection_is_off_by_default_and_keeps_the_ordinary_prepare() -> None:
    text = "A station records temperature."
    build = _build("temperature", hits=(_hit("station", text),), selection=False)

    assert build.fragments == ()
    items = _items(build.context.content)
    assert items[0]["content"] == text


def test_a_budget_that_cannot_hold_a_fragment_delivers_an_empty_context() -> None:
    build = _build(
        "cobalt sensor calibrated",
        max_bytes=512,
        hits=(_hit("cobalt", "The cobalt sensor is calibrated at 37 degrees."),),
    )

    assert build.fragments == ()
    assert build.origins == ()
    assert build.context.status == "empty"
    assert build.context.content is None
    assert build.context.content_bytes == 0


@pytest.mark.parametrize("query", ["cobalt sensor calibrated", "amber valve opens"])
def test_replacing_the_query_changes_the_selected_evidence(query: str) -> None:
    prefix = "General administrative notes. " * 40
    cobalt = _hit("cobalt", f"{prefix}The cobalt sensor is calibrated at 37 degrees.")
    amber = _hit("amber", f"{prefix}The amber valve opens at 19 degrees.")

    build = _build(query, hits=(cobalt, amber))

    assert build.fragments
    expected = "37 degrees" if "cobalt" in query else "19 degrees"
    assert expected in build.fragments[0].text
