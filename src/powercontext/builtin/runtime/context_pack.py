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

"""Chunked, checksummed wire format for one prepared context.

A :class:`ContextPack` carries the exact bytes of one prepared context body as
an ordered tuple of chunks. A :class:`ContextPackDelta` carries the difference
between two packs: how many leading chunks of the base pack the receiver keeps,
and the target chunks that follow them. Both types serialize to a canonical
JSON document, so a receiver rebuilds the target body from the wire bytes
alone, without any sender-side state.

The wire contract is fixed:

* a pack carries exactly ``schema``, ``items``, ``digest`` and ``byte_count``;
* a delta carries exactly ``schema``, ``base_digest``, ``target_digest``,
  ``prefix_items`` and ``appended``;
* ``items`` and ``appended`` are standard Base64 strings, bytes in memory;
* JSON is UTF-8 with sorted keys and ``,``/``:`` separators, without extra
  whitespace;
* the pack digest is ``sha256:<hex>`` over the canonical JSON of the
  ``schema`` and ``items`` fields alone;
* ``byte_count`` is the length of the pack's own serialization, including the
  field itself.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Final

from powercontext.errors import PowerContextError

PACK_SCHEMA: Final = "powercontext.context-pack.v1"
DELTA_SCHEMA: Final = "powercontext.context-pack-delta.v1"
DEFAULT_CHUNK_BYTES: Final = 512
#: The chunk size this module actually uses. The public contract only fixes an
#: upper bound (``chunk_bytes``), and the wire format allows any deterministic
#: chunking. A small capacity keeps the reusable prefix of an unchanged
#: context aligned with the byte where the next context starts to differ, so
#: one changed chunk costs one chunk of retransmission instead of one whole
#: context.
PACK_CHUNK_BYTES: Final = 96

_PACK_FIELDS: Final = frozenset({"schema", "items", "digest", "byte_count"})
_DELTA_FIELDS: Final = frozenset({"schema", "base_digest", "target_digest", "prefix_items", "appended"})
_DIGEST_PATTERN: Final = re.compile(r"\Asha256:[0-9a-f]{64}\Z")


class ContextPackError(PowerContextError, ValueError):
    """Raised when a pack or a delta violates the wire contract."""


def _canonical(payload: Mapping[str, object]) -> bytes:
    """Serialize one payload in the canonical wire form."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _encode_items(items: Iterable[bytes]) -> list[str]:
    return [base64.b64encode(item).decode("ascii") for item in items]


def _decode_items(value: object) -> tuple[bytes, ...]:
    if not isinstance(value, list):
        raise ContextPackError("pack-items-type")
    decoded: list[bytes] = []
    for entry in value:
        if not isinstance(entry, str):
            raise ContextPackError("pack-item-type")
        try:
            decoded.append(base64.b64decode(entry, validate=True))
        except (binascii.Error, ValueError) as error:
            raise ContextPackError("pack-item-encoding") from error
    return tuple(decoded)


def _require_digest(value: object) -> str:
    if not isinstance(value, str) or not _DIGEST_PATTERN.match(value):
        raise ContextPackError("pack-digest-format")
    return value


def _require_bytes(value: object) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, (bytearray, memoryview)):
        return bytes(value)
    raise ContextPackError("pack-item-type")


def _decode_object(data: bytes, /) -> dict[str, object]:
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise ContextPackError("pack-encoding")
    try:
        text = bytes(data).decode("utf-8")
    except UnicodeDecodeError as error:
        raise ContextPackError("pack-encoding") from error
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise ContextPackError("pack-json") from error
    if not isinstance(payload, dict):
        raise ContextPackError("pack-object")
    return payload


def _chunk_limit(chunk_bytes: int) -> int:
    if type(chunk_bytes) is not int or chunk_bytes <= 0:
        raise ContextPackError("pack-chunk-limit")
    return min(chunk_bytes, PACK_CHUNK_BYTES)


def _chunk_text(data: bytes, limit: int) -> tuple[bytes, ...]:
    """Split ``data`` into UTF-8 safe chunks of at most ``limit`` bytes."""
    if not data:
        return ()
    chunks: list[bytes] = []
    start = 0
    total = len(data)
    while start < total:
        end = min(start + limit, total)
        while end < total and (data[end] & 0xC0) == 0x80:
            end -= 1
        if end == start:
            raise ContextPackError("pack-chunk-boundary")
        chunks.append(data[start:end])
        start = end
    return tuple(chunks)


@dataclass(frozen=True)
class ContextPack:
    """One complete prepared context body, chunked and checksummed."""

    items: tuple[bytes, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(_require_bytes(item) for item in self.items))

    @classmethod
    def build(cls, items: Iterable[bytes], /) -> ContextPack:
        """Build one pack from an ordered tuple of byte chunks."""
        return cls(tuple(_require_bytes(item) for item in items))

    @classmethod
    def from_content(cls, content: str, *, chunk_bytes: int = DEFAULT_CHUNK_BYTES) -> ContextPack:
        """Chunk one prepared context body deterministically.

        Every chunk holds valid UTF-8 and stays within the caller's byte
        limit. The chunks concatenate back to the original body. A limit too
        small to hold one character raises :class:`ContextPackError`.
        """
        limit = _chunk_limit(chunk_bytes)
        return cls(_chunk_text(content.encode("utf-8"), limit))

    @classmethod
    def from_bytes(cls, data: bytes, /) -> ContextPack:
        """Decode and verify one serialized pack."""
        payload = _decode_object(data)
        if set(payload) != _PACK_FIELDS:
            raise ContextPackError("pack-fields")
        if payload["schema"] != PACK_SCHEMA:
            raise ContextPackError("pack-schema")
        items = _decode_items(payload["items"])
        digest = _require_digest(payload["digest"])
        if digest != _pack_digest(items):
            raise ContextPackError("pack-digest-mismatch")
        byte_count = payload["byte_count"]
        if type(byte_count) is not int or byte_count < 0:
            raise ContextPackError("pack-byte-count-type")
        pack = cls(items)
        if byte_count != len(pack.to_bytes()):
            raise ContextPackError("pack-byte-count-mismatch")
        return pack

    @property
    def content(self) -> bytes:
        """The exact body this pack carries."""
        return b"".join(self.items)

    @property
    def digest(self) -> str:
        """The ``sha256:<hex>`` digest of this pack's schema and items."""
        return _pack_digest(self.items)

    @property
    def byte_count(self) -> int:
        """The length of this pack's own serialization."""
        return len(self.to_bytes())

    def to_bytes(self) -> bytes:
        """Serialize this pack in the canonical wire form."""
        return _canonical(self._payload())

    def _payload(self) -> dict[str, object]:
        digest = self.digest
        items = _encode_items(self.items)
        payload: dict[str, object] = {
            "byte_count": 0,
            "digest": digest,
            "items": items,
            "schema": PACK_SCHEMA,
        }
        # ``byte_count`` counts its own rendered length, so the value is the
        # fixed point of "the length of the document that carries this number".
        skeleton = len(_canonical(payload))
        count = skeleton - 1
        for _ in range(8):
            candidate = skeleton - 1 + len(str(count))
            if candidate == count:
                break
            count = candidate
        else:  # pragma: no cover - the fixed point is reached within two steps
            raise ContextPackError("pack-byte-count")
        payload["byte_count"] = count
        return payload


@dataclass(frozen=True)
class ContextPackDelta:
    """The difference between two packs, expressed as a reusable prefix."""

    base_digest: str
    target_digest: str
    prefix_items: int
    appended: tuple[bytes, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "appended", tuple(_require_bytes(item) for item in self.appended))

    @classmethod
    def between(cls, base: ContextPack, target: ContextPack, /) -> ContextPackDelta:
        """Describe the change from ``base`` to ``target``.

        The delta keeps the longest run of leading chunks the two packs share
        and carries the remaining target chunks as the suffix.
        """
        if not isinstance(base, ContextPack) or not isinstance(target, ContextPack):
            raise ContextPackError("pack-type")
        shared = 0
        for left, right in zip(base.items, target.items, strict=False):
            if left != right:
                break
            shared += 1
        return cls(
            base_digest=base.digest,
            target_digest=target.digest,
            prefix_items=shared,
            appended=target.items[shared:],
        )

    @classmethod
    def from_bytes(cls, data: bytes, /) -> ContextPackDelta:
        """Decode and verify one serialized delta."""
        payload = _decode_object(data)
        if set(payload) != _DELTA_FIELDS:
            raise ContextPackError("delta-fields")
        if payload["schema"] != DELTA_SCHEMA:
            raise ContextPackError("delta-schema")
        base_digest = _require_digest(payload["base_digest"])
        target_digest = _require_digest(payload["target_digest"])
        prefix_items = payload["prefix_items"]
        if type(prefix_items) is not int or prefix_items < 0:
            raise ContextPackError("delta-prefix-items")
        return cls(
            base_digest=base_digest,
            target_digest=target_digest,
            prefix_items=prefix_items,
            appended=_decode_items(payload["appended"]),
        )

    @property
    def byte_count(self) -> int:
        """The length of this delta's own serialization."""
        return len(self.to_bytes())

    def to_bytes(self) -> bytes:
        """Serialize this delta in the canonical wire form."""
        return _canonical({
            "appended": _encode_items(self.appended),
            "base_digest": self.base_digest,
            "prefix_items": self.prefix_items,
            "schema": DELTA_SCHEMA,
            "target_digest": self.target_digest,
        })


def _pack_digest(items: tuple[bytes, ...]) -> str:
    """Return the digest of the canonical schema-and-items document."""
    canonical = _canonical({"schema": PACK_SCHEMA, "items": _encode_items(items)})
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def apply_context_pack_delta(base: ContextPack, delta: ContextPackDelta, /) -> ContextPack:
    """Rebuild the target pack from a base pack and one delta."""
    if not isinstance(base, ContextPack):
        raise ContextPackError("pack-type")
    if not isinstance(delta, ContextPackDelta):
        raise ContextPackError("delta-type")
    if delta.base_digest != base.digest:
        raise ContextPackError("delta-base-digest")
    if type(delta.prefix_items) is not int or delta.prefix_items < 0:
        raise ContextPackError("delta-prefix-items")
    if delta.prefix_items > len(base.items):
        raise ContextPackError("delta-prefix-range")
    target = ContextPack(base.items[: delta.prefix_items] + delta.appended)
    if target.digest != delta.target_digest:
        raise ContextPackError("delta-target-digest")
    return target


__all__ = [
    "DEFAULT_CHUNK_BYTES",
    "DELTA_SCHEMA",
    "PACK_CHUNK_BYTES",
    "PACK_SCHEMA",
    "ContextPack",
    "ContextPackDelta",
    "ContextPackError",
    "apply_context_pack_delta",
]
