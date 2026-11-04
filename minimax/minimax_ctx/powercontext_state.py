"""Task state persisted through PowerContext.

The context module stores task progress -- the objective, the context pack that
was handed to the assistant, and the verification results -- as PowerContext
Memory entries.  A later run reads the same scope back, so a task can be
resumed from an empty module process instead of from an in-memory cache.

The module talks to a running PowerContext Server over its supported HTTP API
through the official ``powercontext.client`` SDK.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

from minimax_ctx.config import Settings

STATE_KIND = "minimax-sdd-task-state"
SCOPE_TITLE = "MiniMax SDD task board"
SCOPE_SUMMARY = "Task state of the SDD context module for the MiniMax x OceanBase challenge."


@dataclass(slots=True)
class TaskState:
    """The persisted state of one development task."""

    task: str
    phase: str = "planned"
    done: list[str] = field(default_factory=list)
    todo: list[str] = field(default_factory=list)
    verification: list[dict[str, Any]] = field(default_factory=list)
    context_digest: str = ""
    updated_at: str = ""

    def to_json(self) -> str:
        return json.dumps(
            {
                "task": self.task,
                "phase": self.phase,
                "done": self.done,
                "todo": self.todo,
                "verification": self.verification,
                "context_digest": self.context_digest,
                "updated_at": self.updated_at,
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    @staticmethod
    def from_json(raw: str) -> "TaskState":
        payload = json.loads(raw)
        return TaskState(
            task=payload.get("task", ""),
            phase=payload.get("phase", "planned"),
            done=list(payload.get("done", [])),
            todo=list(payload.get("todo", [])),
            verification=list(payload.get("verification", [])),
            context_digest=payload.get("context_digest", ""),
            updated_at=payload.get("updated_at", ""),
        )


def _state_from(text: Any) -> TaskState | None:
    """Return the task state that a Memory entry carries, if it carries one."""

    if not text:
        return None
    try:
        return TaskState.from_json(str(text))
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


class PowerContextState:
    """Save and read task state through a running PowerContext Server."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._scope_id: str | None = None

    # -- helpers ----------------------------------------------------------

    def _client(self) -> Any:
        from powercontext.client import PowerContextClient

        return PowerContextClient(
            base_url=self._settings.powercontext_url,
            token=self._settings.powercontext_token or None,
            timeout=30.0,
            allow_insecure_http=True,
        )

    def scope_id(self) -> str:
        """Return the scope the module owns, resolving it when needed."""

        return asyncio.run(self._scope())

    async def _scope(self) -> str:
        """Return the scope id inside the running event loop."""

        if self._scope_id is None:
            self._scope_id = await self._resolve_scope()
        return self._scope_id

    async def _resolve_scope(self) -> str:
        from powercontext.http._generated.models import CreateScopeRequest

        client = self._client()
        async with client:
            try:
                await client.get_scope(self._settings.powercontext_scope)
                return self._settings.powercontext_scope
            except Exception:  # noqa: BLE001 - the scope is resolved below
                pass
            scopes = await client.list_scopes()
            for item in getattr(scopes, "items", []) or []:
                if getattr(item, "title", "") == SCOPE_TITLE:
                    return str(item.scope_id)
            created = await client.create_scope(
                CreateScopeRequest(
                    title=SCOPE_TITLE,
                    summary=SCOPE_SUMMARY,
                    idempotency_key=f"minimax-sdd-{self._settings.powercontext_scope}",
                )
            )
            return str(created.scope_id)

    async def _remember(self, text: str, reason: str) -> Any:
        from powercontext.http._generated.models import RememberMemoryRequest

        client = self._client()
        async with client:
            return await client.remember_memory(
                RememberMemoryRequest(
                    scope_id=await self._scope(),
                    kind=STATE_KIND,
                    text=text,
                    reason=reason,
                )
            )

    async def _search(self, query: str, limit: int = 10) -> Any:
        from powercontext.http._generated.models import MemorySearchMode, SearchMemoryRequest

        client = self._client()
        async with client:
            return await client.search_memory(
                SearchMemoryRequest(
                    scope_id=await self._scope(),
                    query=query,
                    limit=limit,
                    # This deployment has no embedding model, so the keyword mode
                    # is the one that can answer.
                    mode=MemorySearchMode.FTS,
                )
            )

    async def _list(self) -> Any:
        from powercontext.http._generated.models import ListMemoryEntriesRequest

        client = self._client()
        async with client:
            scope_id = await self._scope()
            return await client.list_memory_entries(
                ListMemoryEntriesRequest(scope_id=scope_id, include_inactive=False)
            )

    # -- public API -------------------------------------------------------

    def save(self, state: TaskState) -> Any:
        """Persist one task state and return the server response."""

        return asyncio.run(self._remember(state.to_json(), f"task state: {state.task}"))

    def load(self, task: str) -> TaskState | None:
        """Read the newest saved state for ``task`` back, if there is one."""

        states = self._states_for(task)
        if not states:
            return None
        return states[-1]

    def _states_for(self, task: str) -> list[TaskState]:
        """Return every saved state that belongs to ``task``, oldest first."""

        found: list[TaskState] = []
        try:
            response = asyncio.run(self._search(task, limit=20))
            hits = getattr(response, "hits", []) or []
        except Exception:  # noqa: BLE001 - fall back to the entry listing
            hits = []
        for hit in hits:
            state = _state_from(getattr(hit, "text", None))
            if state is not None and state.task == task:
                found.append(state)
        if not found:
            try:
                response = asyncio.run(self._list())
            except Exception:  # noqa: BLE001 - the caller reports an empty result
                return found
            for entry in getattr(response, "entries", []) or []:
                state = _state_from(getattr(entry, "text", None))
                if state is not None and state.task == task:
                    found.append(state)
        return found

    def list_states(self) -> list[TaskState]:
        """Return every saved task state in the scope."""

        response = asyncio.run(self._list())
        states: list[TaskState] = []
        for entry in getattr(response, "entries", []) or []:
            state = _state_from(getattr(entry, "text", None))
            if state is not None:
                states.append(state)
        return states

    def health(self) -> dict[str, Any]:
        """Report whether the configured PowerContext Server answers."""

        from powercontext.client import PowerContextClient

        client = self._client()

        async def _probe() -> dict[str, Any]:
            async with client:
                live = await client.get_liveness()
                ready = await client.get_readiness()
                return {"live": live.model_dump(), "ready": ready.model_dump(), "scope_id": await self._scope()}

        try:
            return asyncio.run(_probe())
        except Exception as error:  # noqa: BLE001 - the probe reports the failure
            return {"error": f"{type(error).__name__}: {error}"}


__all__ = ["SCOPE_TITLE", "STATE_KIND", "PowerContextState", "TaskState"]
