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

"""Wire contract, reconstruction, and Runtime integration of the Context Pack."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json

import pytest

from powercontext.builtin.artifacts.memory import MemoryCandidateRequest, MemoryEntryInput
from powercontext.builtin.runtime import BuiltinConfig, CaptureSource, PrepareContextRequest, open_builtin_runtime
from powercontext.builtin.runtime.context_pack import (
    DEFAULT_CHUNK_BYTES,
    PACK_CHUNK_BYTES,
    ContextPack,
    ContextPackDelta,
    ContextPackError,
    apply_context_pack_delta,
)
from powercontext.builtin.scope import ScopeDraft
from powercontext.builtin.sources import ContentSource

_PACK_SCHEMA = "powercontext.context-pack.v1"
_DELTA_SCHEMA = "powercontext.context-pack-delta.v1"
_TURNS = (
    "Project Manager: What are your experiences with remote controls ?",
    "Industrial Designer: The menu display should stay simple .",
    "Project Manager: The price range targets the younger market .",
)


class _ContentCandidatePipeline:
    async def extract(self, request: MemoryCandidateRequest, /) -> tuple[MemoryEntryInput, ...]:
        return tuple(
            MemoryEntryInput(kind="conversation-turn", text=source.content, sources=(source,))
            for source in request.sources
            if isinstance(source, ContentSource)
        )


def _oracle_digest(items: tuple[bytes, ...]) -> str:
    unsigned = {
        "schema": _PACK_SCHEMA,
        "items": [base64.b64encode(item).decode("ascii") for item in items],
    }
    canonical = json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def test_pack_wire_payload_matches_an_independent_oracle() -> None:
    items = (b"alpha\n", "海洋".encode())
    pack = ContextPack.build(items)
    payload = json.loads(pack.to_bytes())

    assert set(payload) == {"schema", "items", "digest", "byte_count"}
    assert payload["schema"] == _PACK_SCHEMA
    assert payload["items"] == [base64.b64encode(item).decode("ascii") for item in items]
    assert payload["digest"] == _oracle_digest(items)
    assert payload["byte_count"] == len(pack.to_bytes())
    assert pack.byte_count == len(pack.to_bytes())
    assert pack.to_bytes() == json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def test_pack_round_trips_through_the_wire() -> None:
    pack = ContextPack.build((b"alpha", b"beta"))

    assert ContextPack.from_bytes(pack.to_bytes()) == pack
    assert ContextPack.from_bytes(pack.to_bytes()).digest == pack.digest


@pytest.mark.parametrize("chunk_bytes", [16, 64, DEFAULT_CHUNK_BYTES, 4096])
def test_from_content_chunks_are_bounded_valid_utf8_and_deterministic(chunk_bytes: int) -> None:
    content = "alpha 海洋 beta " * 20

    pack = ContextPack.from_content(content, chunk_bytes=chunk_bytes)

    assert b"".join(pack.items).decode("utf-8") == content
    assert all(0 < len(item) <= min(chunk_bytes, PACK_CHUNK_BYTES) for item in pack.items)
    assert all(item.decode("utf-8") for item in pack.items)
    assert ContextPack.from_content(content, chunk_bytes=chunk_bytes).items == pack.items


def test_from_content_rejects_a_limit_that_cannot_hold_one_character() -> None:
    with pytest.raises(ContextPackError):
        ContextPack.from_content("海", chunk_bytes=2)

    with pytest.raises(ContextPackError):
        ContextPack.from_content("alpha", chunk_bytes=0)


def test_empty_pack_is_valid_and_carries_no_content() -> None:
    empty = ContextPack.from_content("")

    assert empty.items == ()
    assert empty.content == b""
    assert ContextPack.from_bytes(empty.to_bytes()) == empty


@pytest.mark.parametrize(
    "target_items",
    [
        (b"new", b"b", b"old"),
        (b"a", b"new", b"old"),
        (b"a", b"b", b"new"),
        (b"a",),
        (),
    ],
)
def test_delta_rebuilds_a_change_at_any_position(target_items: tuple[bytes, ...]) -> None:
    base = ContextPack.build((b"a", b"b", b"old"))
    target = ContextPack.build(target_items)

    delta = ContextPackDelta.between(base, target)
    rebuilt = apply_context_pack_delta(
        ContextPack.from_bytes(base.to_bytes()),
        ContextPackDelta.from_bytes(delta.to_bytes()),
    )

    assert rebuilt.to_bytes() == target.to_bytes()
    assert rebuilt.content == target.content
    assert delta.byte_count == len(delta.to_bytes())
    assert 0 <= delta.prefix_items <= len(base.items)


def test_delta_keeps_the_chunks_two_contexts_share() -> None:
    shared = "alpha beta gamma delta "
    base = ContextPack.from_content(shared * 20 + "epsilon")
    target = ContextPack.from_content(shared * 20 + "omega")

    delta = ContextPackDelta.between(base, target)

    assert delta.prefix_items > 0
    assert base.items[: delta.prefix_items] == target.items[: delta.prefix_items]
    assert base.items[: delta.prefix_items] + delta.appended == target.items


def test_delta_rejects_a_foreign_base_and_a_wrong_target_digest() -> None:
    base = ContextPack.build((b"one",))
    delta = ContextPackDelta.between(base, ContextPack.build((b"one", b"two")))

    with pytest.raises(ContextPackError):
        apply_context_pack_delta(ContextPack.build((b"other",)), delta)

    wrong_target = ContextPackDelta(base.digest, "sha256:" + "0" * 64, 1, ())
    with pytest.raises(ContextPackError):
        apply_context_pack_delta(base, wrong_target)


@pytest.mark.parametrize("prefix", [True, 1.5, -1, "1", None])
def test_delta_rejects_a_prefix_that_is_not_a_non_negative_integer(prefix: object) -> None:
    base = ContextPack.build((b"alpha",))
    delta = ContextPackDelta.between(base, ContextPack.build((b"alpha", b"beta")))
    payload = json.loads(delta.to_bytes())
    payload["prefix_items"] = prefix

    with pytest.raises(ContextPackError):
        ContextPackDelta.from_bytes(json.dumps(payload).encode())


def test_delta_rejects_a_prefix_beyond_the_base() -> None:
    base = ContextPack.build((b"alpha",))
    delta = ContextPackDelta(base.digest, base.digest, 2, ())

    with pytest.raises(ContextPackError):
        apply_context_pack_delta(base, delta)


@pytest.mark.parametrize("field", ["schema", "items", "digest", "byte_count"])
def test_pack_decoder_rejects_a_missing_field(field: str) -> None:
    pack = ContextPack.build((b"alpha",))
    payload = json.loads(pack.to_bytes())
    del payload[field]

    with pytest.raises(ContextPackError):
        ContextPack.from_bytes(json.dumps(payload).encode())


def test_pack_decoder_rejects_an_extra_field_and_a_wrong_byte_count() -> None:
    pack = ContextPack.build((b"alpha",))
    payload = json.loads(pack.to_bytes())

    with pytest.raises(ContextPackError):
        ContextPack.from_bytes(json.dumps({**payload, "extra": 1}).encode())

    with pytest.raises(ContextPackError):
        ContextPack.from_bytes(json.dumps({**payload, "byte_count": payload["byte_count"] + 1}).encode())


def test_pack_decoder_rejects_a_tampered_digest_and_an_illegal_encoding() -> None:
    pack = ContextPack.build((b"alpha",))

    with pytest.raises(ContextPackError):
        ContextPack.from_bytes(pack.to_bytes().replace(pack.digest.encode(), b"sha256:" + b"0" * 64))

    payload = json.loads(pack.to_bytes())
    payload["items"] = ["not base64!"]
    with pytest.raises(ContextPackError):
        ContextPack.from_bytes(json.dumps(payload).encode())

    with pytest.raises(ContextPackError):
        ContextPack.from_bytes(b"{not json")


def test_delta_decoder_rejects_missing_and_extra_fields() -> None:
    base = ContextPack.build((b"alpha",))
    delta = ContextPackDelta.between(base, ContextPack.build((b"alpha", b"beta")))
    payload = json.loads(delta.to_bytes())

    for field in payload:
        incomplete = {key: value for key, value in payload.items() if key != field}
        with pytest.raises(ContextPackError):
            ContextPackDelta.from_bytes(json.dumps(incomplete).encode())

    with pytest.raises(ContextPackError):
        ContextPackDelta.from_bytes(json.dumps({**payload, "extra": 1}).encode())


def test_delta_wire_carries_exactly_the_contracted_fields() -> None:
    base = ContextPack.build((b"alpha",))
    delta = ContextPackDelta.between(base, ContextPack.build((b"alpha", b"beta")))
    payload = json.loads(delta.to_bytes())

    assert set(payload) == {"schema", "base_digest", "target_digest", "prefix_items", "appended"}
    assert payload["schema"] == _DELTA_SCHEMA
    assert payload["base_digest"] == base.digest
    assert payload["target_digest"] == ContextPack.build((b"alpha", b"beta")).digest
    assert type(payload["prefix_items"]) is int
    assert "byte_count" not in payload


def test_runtime_prepare_incremental_rebuilds_the_plain_prepare_body() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(), candidate_pipeline=_ContentCandidatePipeline()) as runtime:
            scope = await runtime.scopes.create(
                ScopeDraft(
                    title="Context pack",
                    summary="Context pack runtime",
                    idempotency_key="context-pack-runtime",
                )
            )
            sources = runtime.sources.for_scope(scope.scope_id)
            memory = runtime.memory.for_scope(scope.scope_id)
            context = runtime.context.for_scope(scope.scope_id)
            await sources.capture(CaptureSource(source_id="turn-1", content=_TURNS[0], metadata={}))
            while (await memory.flush()).processed:
                pass

            request = PrepareContextRequest(query="remote", max_bytes=4096)
            first = await context.prepare_incremental(request)
            assert isinstance(first, ContextPack)
            assert first.content.decode("utf-8")

            await sources.capture(CaptureSource(source_id="turn-2", content=_TURNS[1], metadata={}))
            while (await memory.flush()).processed:
                pass

            second = await context.prepare_incremental(request, base_pack=first)
            assert isinstance(second, ContextPackDelta)
            expected = await context.prepare(request)
            assert expected.content

        rebuilt = apply_context_pack_delta(
            ContextPack.from_bytes(first.to_bytes()),
            ContextPackDelta.from_bytes(second.to_bytes()),
        )
        assert rebuilt.content.decode("utf-8") == expected.content

    asyncio.run(scenario())


def test_runtime_prepare_incremental_keeps_the_legal_empty_result() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(), candidate_pipeline=_ContentCandidatePipeline()) as runtime:
            scope = await runtime.scopes.create(
                ScopeDraft(
                    title="Context pack empty",
                    summary="Context pack empty result",
                    idempotency_key="context-pack-empty",
                )
            )
            context = runtime.context.for_scope(scope.scope_id)
            request = PrepareContextRequest(query="remote", max_bytes=4096)

            empty = await context.prepare_incremental(request)
            plain = await context.prepare(request)

            assert isinstance(empty, ContextPack)
            assert empty.items == ()
            assert empty.content == b""
            assert plain.content is None

            delta = await context.prepare_incremental(request, base_pack=empty)
            assert isinstance(delta, ContextPackDelta)
            assert apply_context_pack_delta(empty, delta).to_bytes() == empty.to_bytes()

    asyncio.run(scenario())


def test_repeated_rounds_transfer_less_than_the_full_context() -> None:
    base = ContextPack.from_content("alpha " * 200)
    unchanged = ContextPack.from_content("alpha " * 200)
    changed = ContextPack.from_content("alpha " * 199 + "omega ")

    unchanged_delta = ContextPackDelta.between(base, unchanged)
    changed_delta = ContextPackDelta.between(base, changed)

    assert len(unchanged_delta.to_bytes()) < len(base.to_bytes())
    assert len(changed_delta.to_bytes()) < len(base.to_bytes())
    assert apply_context_pack_delta(base, unchanged_delta).to_bytes() == unchanged.to_bytes()
    assert apply_context_pack_delta(base, changed_delta).to_bytes() == changed.to_bytes()
