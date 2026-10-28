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

"""Rerank coarse Memory candidates with an external HTTP scoring service.

The service scores a query against candidate texts. Its batch limit is smaller
than a retrieval pool, so the adapter splits the pool into batches, keeps every
candidate's global rank across the split, and then makes **one** selection over
the complete pool. Batches never decide the final order on their own: a
candidate from a late batch may outrank a candidate from the first batch, and
candidates that tie on score keep their original rank order.
"""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import ValidationError

from powercontext.builtin.artifacts.memory.models import MemoryHit
from powercontext.builtin.artifacts.memory.reranking import MemoryRerankDecision
from powercontext.builtin.inference import InferenceUsage
from powercontext.service.rerank.protocol import (
    HTTPRerankCandidate,
    HTTPRerankRequest,
    HTTPRerankResponse,
    render_request_payload,
)

DEFAULT_BATCH_SIZE = 32
DEFAULT_POLICY_ID = "http-cross-encoder-v1"

_Batch = tuple[tuple[int, str], ...]


class HTTPMemoryReranker:
    """Select final Memory hits with an external HTTP reranking service.

    The caller owns the :class:`httpx.AsyncClient`, so this adapter never opens
    a connection of its own and never changes the caller's timeout, retries or
    transport. It only owns the protocol: batching, rank bookkeeping, response
    validation and the global selection.
    """

    def __init__(
        self,
        endpoint: str,
        *,
        client: httpx.AsyncClient,
        batch_size: int = DEFAULT_BATCH_SIZE,
        policy_id: str = DEFAULT_POLICY_ID,
    ) -> None:
        """Bind one reranking endpoint, its client and the per-request batch limit."""
        if not isinstance(endpoint, str) or not endpoint:
            raise ValueError("rerank endpoint must be a non-empty string")  # noqa: TRY003
        if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size < 1:
            raise ValueError("rerank batch size must be a positive integer")  # noqa: TRY003
        if not isinstance(policy_id, str) or not policy_id:
            raise ValueError("rerank policy id must be a non-empty string")  # noqa: TRY003
        self._endpoint = endpoint
        self._client = client
        self._batch_size = batch_size
        self.policy_id = policy_id

    async def rerank(
        self,
        query: str,
        candidates: tuple[MemoryHit, ...],
        limit: int,
        /,
    ) -> MemoryRerankDecision:
        """Return the globally best-scoring original ranks of one coarse pool.

        The returned ranks are one-based positions in ``candidates``, so the
        caller can still resolve every selected result to its original Memory
        entry and entry version.
        """
        _validate_bounds(len(candidates), limit)
        batches = _split_batches(candidates, self._batch_size)
        scores: dict[int, float] = {}
        requests = 0
        for batch in batches:
            payload = _render_request(query, batch)
            requests += 1
            response = await self._client.post(self._endpoint, json=payload)
            scores.update(_read_scores(response, batch))
        selected = _select_ranks(scores, limit=limit)
        return MemoryRerankDecision(
            selected_ranks=selected,
            usage=InferenceUsage(requests=requests),
            discarded_rank_count=len(candidates) - len(selected),
        )


def _split_batches(candidates: tuple[MemoryHit, ...], batch_size: int) -> tuple[_Batch, ...]:
    """Split the pool into batches of at most ``batch_size`` candidates.

    Every global rank is kept and sent exactly once, and no batch renumbers a
    candidate: the rank in a request is the rank in the complete pool.
    """
    items = tuple((rank, hit.text) for rank, hit in enumerate(candidates, start=1))
    return tuple(items[start : start + batch_size] for start in range(0, len(items), batch_size))


def _render_request(query: str, batch: _Batch) -> dict[str, Any]:
    """Validate and render one batch request in the documented wire shape."""
    try:
        request = HTTPRerankRequest(
            query=query,
            candidates=tuple(HTTPRerankCandidate(rank=rank, text=text) for rank, text in batch),
        )
    except ValidationError as error:
        raise ValueError(f"invalid rerank request: {error}") from error  # noqa: TRY003
    return render_request_payload(request)


def _read_scores(response: httpx.Response, batch: _Batch) -> dict[int, float]:
    """Validate one batch response and map its scores back to global ranks.

    The response may arrive in any order, but it must cover exactly the ranks
    of this batch, once each. A missing, duplicated, foreign or extra-ranked
    score is a protocol violation, not a partial result.
    """
    if not response.is_success:
        response.raise_for_status()
        raise httpx.HTTPStatusError(  # noqa: TRY003
            f"rerank service returned HTTP {response.status_code}",
            request=response.request,
            response=response,
        )
    try:
        payload = response.json()
    except ValueError as error:
        raise ValueError("rerank service returned a body that is not valid JSON") from error  # noqa: TRY003
    try:
        parsed = HTTPRerankResponse.model_validate(payload)
    except ValidationError as error:
        raise ValueError(f"rerank service returned an invalid response schema: {error}") from error  # noqa: TRY003
    observed = [score.rank for score in parsed.scores]
    if len(set(observed)) != len(observed):
        raise ValueError("rerank service returned duplicate ranks for one batch")  # noqa: TRY003
    expected = {rank for rank, _ in batch}
    if set(observed) != expected:
        raise ValueError("rerank service did not cover the requested batch ranks exactly once")  # noqa: TRY003
    return {score.rank: score.score for score in parsed.scores}


def _select_ranks(scores: dict[int, float], *, limit: int) -> tuple[int, ...]:
    """Select the globally best ranks: score descending, then original rank ascending."""
    ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    return tuple(rank for rank, _ in ordered[:limit])


def _validate_bounds(candidate_count: int, limit: int) -> None:
    """Reject pools and limits that cannot produce a bounded selection."""
    if candidate_count < 1:
        raise ValueError("rerank requires at least one candidate")  # noqa: TRY003
    if limit < 1 or limit > candidate_count:
        raise ValueError("rerank limit must be between one and the candidate count")  # noqa: TRY003


__all__ = ["DEFAULT_BATCH_SIZE", "DEFAULT_POLICY_ID", "HTTPMemoryReranker"]
