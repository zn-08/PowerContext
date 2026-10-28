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

"""Concurrent embedding request coalescing with a bounded vector cache.

One wrapper owns every embedding caller that shares its event loop. Identical
texts are embedded once, concurrent callers wait for the same provider request,
and successful vectors are reused from a bounded LRU cache.

The wrapper stays transparent for callers: it exposes the wrapped model's
``profile`` and returns one vector per input text, in the caller's own input
order and with repeated positions preserved.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from itertools import islice
from typing import TYPE_CHECKING

from powercontext.builtin.inference.models import EmbeddingResult, EmbeddingVector, InferenceUsage

if TYPE_CHECKING:
    from powercontext.builtin.artifacts.memory.models import EmbeddingProfile
    from powercontext.builtin.inference.protocols import EmbeddingModel

__all__ = ["CoalescingEmbeddingModel"]


@dataclass(slots=True)
class _Batch:
    """One provider request together with the texts it owns."""

    batch_id: int
    texts: tuple[str, ...]
    usage: InferenceUsage = field(default_factory=lambda: InferenceUsage(requests=0))


# One resolved text: its vector and the provider request that produced it.
_Resolution = tuple[EmbeddingVector, _Batch]


def _consume_outcome(future: asyncio.Future[object]) -> None:
    """Retrieve a finished outcome so an unattended waiter cannot leak a warning."""

    if not future.cancelled():
        future.exception()


def _summarize(batches: Iterable[_Batch]) -> InferenceUsage:
    """Aggregate the provider usage of the distinct batches one caller waited for."""

    requests = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    for batch in batches:
        usage = batch.usage
        requests += usage.requests
        if usage.input_tokens is not None:
            input_tokens = (input_tokens or 0) + usage.input_tokens
        if usage.output_tokens is not None:
            output_tokens = (output_tokens or 0) + usage.output_tokens
    return InferenceUsage(requests=requests, input_tokens=input_tokens, output_tokens=output_tokens)


class CoalescingEmbeddingModel:
    """Merge concurrent embedding requests and reuse successful vectors.

    The wrapped model keeps ownership of the provider call; this wrapper only
    decides which texts travel together, which caller waits for which request,
    and which vector may be reused later.
    """

    def __init__(
        self,
        model: EmbeddingModel,
        *,
        max_batch_size: int = 64,
        coalesce_ms: float = 5.0,
        cache_size: int = 1024,
    ) -> None:
        """Wrap one embedding model with request coalescing and vector caching."""

        self._model = model
        self.profile: EmbeddingProfile = model.profile
        self._max_batch_size = max(1, int(max_batch_size))
        self._coalesce_seconds = max(0.0, float(coalesce_ms)) / 1000.0
        self._cache_size = max(0, int(cache_size))
        self._cache: OrderedDict[str, EmbeddingVector] = OrderedDict()
        self._pending: OrderedDict[str, None] = OrderedDict()
        self._pending_since: float | None = None
        self._futures: dict[str, asyncio.Future[_Resolution]] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake: asyncio.Event | None = None
        self._dispatcher: asyncio.Task[None] | None = None
        self._batch_counter = 0

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        """Return one vector per input text, sharing provider work with other callers."""

        ordered = tuple(texts)
        if not ordered:
            return EmbeddingResult(vectors=(), usage=InferenceUsage(requests=0))
        loop = asyncio.get_running_loop()
        self._bind(loop)
        vectors: dict[str, EmbeddingVector] = {}
        waiting: list[tuple[str, asyncio.Future[_Resolution]]] = []
        for text in dict.fromkeys(ordered):
            cached = self._cached(text)
            if cached is not None:
                vectors[text] = cached
                continue
            future = self._futures.get(text)
            if future is None:
                future = loop.create_future()
                self._futures[text] = future
                self._enqueue(text, loop)
            waiting.append((text, future))
        if not waiting:
            return EmbeddingResult(vectors=tuple(vectors[text] for text in ordered), usage=InferenceUsage(requests=0))
        resolved = await asyncio.gather(*(self._shield(future) for _, future in waiting))
        batches: dict[int, _Batch] = {}
        for (text, _), (vector, batch) in zip(waiting, resolved, strict=True):
            vectors[text] = vector
            batches[batch.batch_id] = batch
        return EmbeddingResult(
            vectors=tuple(vectors[text] for text in ordered),
            usage=_summarize(batches.values()),
        )

    async def aclose(self) -> None:
        """Stop the dispatcher and release every request that is still pending."""

        dispatcher = self._dispatcher
        self._dispatcher = None
        if dispatcher is not None and not dispatcher.done():
            dispatcher.cancel()
            await asyncio.gather(dispatcher, return_exceptions=True)
        running = tuple(self._tasks)
        for task in running:
            task.cancel()
        if running:
            await asyncio.gather(*running, return_exceptions=True)
        self._pending.clear()
        self._pending_since = None
        for future in tuple(self._futures.values()):
            future.cancel()
        self._futures.clear()

    def _shield(self, future: asyncio.Future[_Resolution]) -> asyncio.Future[_Resolution]:
        """Wait for one shared request without letting this caller's cancellation end it."""

        shielded = asyncio.shield(future)
        shielded.add_done_callback(_consume_outcome)
        return shielded

    def _bind(self, loop: asyncio.AbstractEventLoop) -> None:
        """Adopt the running loop and drop work that belonged to a closed one."""

        if self._loop is loop:
            return
        self._loop = loop
        self._wake = asyncio.Event()
        self._dispatcher = None
        self._pending.clear()
        self._pending_since = None
        self._futures.clear()
        self._tasks.clear()

    def _wake_event(self) -> asyncio.Event:
        """Return the dispatcher wake-up signal of the running loop."""

        event = self._wake
        if event is None:
            event = asyncio.Event()
            self._wake = event
        return event

    def _enqueue(self, text: str, loop: asyncio.AbstractEventLoop) -> None:
        """Register one text for the next provider request and wake the dispatcher."""

        if not self._pending:
            self._pending_since = loop.time()
        self._pending[text] = None
        self._wake_event().set()
        self._ensure_dispatcher(loop)

    def _ensure_dispatcher(self, loop: asyncio.AbstractEventLoop) -> None:
        """Start the dispatcher unless one is already turning pending texts into batches."""

        dispatcher = self._dispatcher
        if dispatcher is not None and not dispatcher.done():
            return
        self._dispatcher = loop.create_task(self._dispatch())

    async def _dispatch(self) -> None:
        """Turn the texts collected inside the merge window into bounded provider requests."""

        loop = asyncio.get_running_loop()
        while True:
            await self._wake_event().wait()
            self._wake_event().clear()
            while self._pending:
                delay = self._merge_delay(loop)
                if delay > 0:
                    await asyncio.sleep(delay)
                    continue
                self._form_batch(loop)
            # A text that arrived while the last batch was formed re-arms the
            # dispatcher; the check and the exit below never await in between,
            # so a late registration cannot be dropped.
            if self._wake_event().is_set():
                continue
            return

    def _merge_delay(self, loop: asyncio.AbstractEventLoop) -> float:
        """Return the time left in the merge window that opened with the first pending text."""

        since = self._pending_since
        if since is None:
            return 0.0
        return max(0.0, since + self._coalesce_seconds - loop.time())

    def _form_batch(self, loop: asyncio.AbstractEventLoop) -> None:
        """Move the oldest pending texts into one bounded provider request."""

        texts = tuple(islice(self._pending, self._max_batch_size))
        for text in texts:
            del self._pending[text]
        if not self._pending:
            self._pending_since = None
        self._batch_counter += 1
        batch = _Batch(batch_id=self._batch_counter, texts=texts)
        task = loop.create_task(self._execute(batch))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _execute(self, batch: _Batch) -> None:
        """Run one provider request and publish its vectors to every waiter."""

        try:
            result = await self._model.embed(batch.texts)
        except asyncio.CancelledError as error:
            self._fail(batch, error)
            raise
        except Exception as error:
            self._fail(batch, error)
            return
        vectors = tuple(result.vectors)
        if len(vectors) != len(batch.texts):
            self._fail(batch, ValueError("embedding result does not match the requested text count"))
            return
        batch.usage = result.usage
        for text, vector in zip(batch.texts, vectors, strict=True):
            self._store(text, vector)
            future = self._futures.pop(text, None)
            if future is not None and not future.done():
                future.set_result((vector, batch))

    def _fail(self, batch: _Batch, error: BaseException) -> None:
        """Publish one failure to every waiter of the batch without caching it."""

        for text in batch.texts:
            future = self._futures.pop(text, None)
            if future is None or future.done():
                continue
            if isinstance(error, asyncio.CancelledError):
                future.cancel()
                continue
            future.set_exception(error)
            future.exception()

    def _cached(self, text: str) -> EmbeddingVector | None:
        """Return one cached vector and refresh its most-recently-used position."""

        if self._cache_size <= 0:
            return None
        vector = self._cache.get(text)
        if vector is None:
            return None
        self._cache.move_to_end(text)
        return vector

    def _store(self, text: str, vector: EmbeddingVector) -> None:
        """Cache one successful vector within the configured capacity."""

        if self._cache_size <= 0:
            return
        self._cache[text] = vector
        self._cache.move_to_end(text)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
