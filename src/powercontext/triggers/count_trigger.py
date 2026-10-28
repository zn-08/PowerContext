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

"""Pure Source-count admission policy for automatic processing.

A count threshold lets one scope start processing as soon as enough new
Source journal entries exist after its last successful cursor. The policy
is deliberately Sans-I/O: it observes a pending count and reports whether
one automatic processing window may be accepted. Cursors, persistence and
Worker lifecycles stay with the owning runtime.

The counting rule is journal based. Only entries that follow the last
successfully committed cursor of one scope and one processing family are
pending, so a replayed Source identity never adds pending work.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from powercontext.triggers.models import PolicyTransition

SOURCE_COUNT_TRIGGER_NAME = "source-count"


def validate_source_count_threshold(value: Any) -> int | None:
    """Accept only ``None`` or a strict positive integer threshold.

    ``None`` keeps the count trigger disabled, which preserves the original
    time based behaviour. Booleans, strings, floats, zero and negative
    values are rejected so one configuration value can never silently change
    the admission meaning of a scope.
    """

    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("Source count threshold must be a positive integer")  # noqa: TRY003
    return value


def pending_source_count(journal_head: int, cursor: int) -> int:
    """Return the new journal entries that follow one successful cursor.

    ``journal_head`` is the current end of the scope's Source journal and
    ``cursor`` is the position this processor last committed successfully.
    A cursor ahead of the head cannot produce negative pending work.
    """

    return max(0, journal_head - cursor)


class SourceCountSignal(BaseModel):
    """One observation of one scope's pending Source entries."""

    pending: int = Field(ge=0)
    threshold: int | None = Field(default=None, ge=1)
    # A caller that already satisfies another admission condition, such as
    # the time condition or an explicit flush, bypasses the count threshold.
    forced: bool = False


class SourceCountState(BaseModel):
    """The admission state of one scope and one processing family."""

    pending: int = Field(default=0, ge=0)
    admitted: int = Field(default=0, ge=0)


class AdmitSourceCount(BaseModel):
    """Accept one automatic processing window for the counted scope."""

    pending: int = Field(ge=0)
    threshold: int | None = None
    forced: bool = False


class SourceCountTrigger:
    """Decide whether new Source entries reached one admission threshold."""

    def initial_state(self) -> SourceCountState:
        """Return the state used before the first activation."""

        return SourceCountState()

    def activate(
        self,
        signal: SourceCountSignal,
        state: SourceCountState,
        /,
    ) -> PolicyTransition[SourceCountState, AdmitSourceCount]:
        """Evaluate one observation without persisting state or executing actions."""

        pending = signal.pending
        if not signal.forced and (signal.threshold is None or pending < signal.threshold):
            # Below the threshold the scope keeps waiting for new input, the
            # time condition or an explicit flush.
            return PolicyTransition(state=state.model_copy(update={"pending": pending}))
        return PolicyTransition(
            state=SourceCountState(pending=pending, admitted=state.admitted + 1),
            actions=(
                AdmitSourceCount(
                    pending=pending,
                    threshold=signal.threshold,
                    forced=signal.forced,
                ),
            ),
        )


__all__ = [
    "SOURCE_COUNT_TRIGGER_NAME",
    "AdmitSourceCount",
    "SourceCountSignal",
    "SourceCountState",
    "SourceCountTrigger",
    "pending_source_count",
    "validate_source_count_threshold",
]
