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

"""Wire protocol of the external HTTP reranking service.

The protocol is deliberately small: one query plus the candidates of a single
batch go out, one score per candidate comes back. Ranks are global one-based
positions in the complete candidate pool, so a batch never renumbers anything.
"""

from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator


class _StrictModel(BaseModel):
    """Reject unknown fields so a drifting payload fails instead of passing silently."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class HTTPRerankCandidate(_StrictModel):
    """One candidate text addressed by its global one-based rank."""

    rank: StrictInt = Field(ge=1)
    text: str = Field(min_length=1)


class HTTPRerankRequest(_StrictModel):
    """One query and the candidates of exactly one batch."""

    query: str = Field(min_length=1)
    candidates: tuple[HTTPRerankCandidate, ...] = Field(min_length=1)


class HTTPRerankScore(_StrictModel):
    """One service score bound to the global rank of the scored candidate."""

    rank: StrictInt = Field(ge=1)
    score: float

    @field_validator("score", mode="before")
    @classmethod
    def _finite_number(cls, value: Any) -> Any:
        """Accept finite JSON numbers only: NaN and Infinity are not scores."""
        if isinstance(value, bool) or not isinstance(value, int | float):
            # A pydantic validator must raise ValueError: a TypeError would escape the model.
            raise ValueError("score must be a JSON number")  # noqa: TRY003, TRY004
        if not math.isfinite(value):
            raise ValueError("score must be a finite number")  # noqa: TRY003
        return float(value)


class HTTPRerankResponse(_StrictModel):
    """The scores of exactly one batch, in any order."""

    scores: tuple[HTTPRerankScore, ...]


def render_request_payload(request: HTTPRerankRequest) -> dict[str, Any]:
    """Render one validated request in the documented wire shape."""
    return {
        "query": request.query,
        "candidates": [{"rank": candidate.rank, "text": candidate.text} for candidate in request.candidates],
    }


__all__ = [
    "HTTPRerankCandidate",
    "HTTPRerankRequest",
    "HTTPRerankResponse",
    "HTTPRerankScore",
    "render_request_payload",
]
