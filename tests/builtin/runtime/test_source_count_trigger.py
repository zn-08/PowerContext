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

"""Public Source-count admission configuration and its pure policy."""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from powercontext.builtin.runtime.artifact_processing import ArtifactProcessingBinding
from powercontext.builtin.runtime.composition import _source_count_threshold
from powercontext.builtin.runtime.config import RuntimeConfig
from powercontext.triggers.count_trigger import (
    SourceCountSignal,
    SourceCountTrigger,
    pending_source_count,
)

_COUNT_FIELDS = ("memory_source_count_threshold", "topic_memory_source_count_threshold")


@pytest.mark.parametrize("field", _COUNT_FIELDS)
def test_source_count_threshold_is_disabled_by_default(field: str) -> None:
    assert getattr(RuntimeConfig(), field) is None


@pytest.mark.parametrize("field", _COUNT_FIELDS)
@pytest.mark.parametrize("value", [True, False, 0, -1, 1.0, "3"])
def test_source_count_threshold_rejects_every_non_positive_integer(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        RuntimeConfig(**{field: value})


@pytest.mark.parametrize("field", _COUNT_FIELDS)
@pytest.mark.parametrize("value", [None, 1, 3, 100])
def test_source_count_threshold_accepts_disabled_or_positive_integers(field: str, value: int | None) -> None:
    assert getattr(RuntimeConfig(**{field: value}), field) == value


def test_source_count_threshold_maps_each_family_to_its_own_configuration() -> None:
    runtime = RuntimeConfig(memory_source_count_threshold=3, topic_memory_source_count_threshold=5)

    assert _source_count_threshold(runtime, "memory") == 3
    assert _source_count_threshold(runtime, "topic-memory") == 5
    assert _source_count_threshold(runtime, "experience") is None


def test_binding_rejects_a_count_threshold_that_is_not_a_positive_integer() -> None:
    with pytest.raises(ValueError, match="source_count_threshold"):
        ArtifactProcessingBinding(
            binding_name="count-binding",
            artifact_family="memory",
            launcher=Mock(),
            source_count_threshold=0,
        )


def test_pending_source_count_never_exceeds_the_journal_head() -> None:
    assert pending_source_count(5, 2) == 3
    assert pending_source_count(2, 5) == 0
    assert pending_source_count(0, 0) == 0


def test_source_count_trigger_waits_below_the_threshold_and_admits_at_it() -> None:
    trigger = SourceCountTrigger()
    state = trigger.initial_state()

    below = trigger.activate(SourceCountSignal(pending=2, threshold=3), state)
    assert not below.actions
    assert below.state.pending == 2

    reached = trigger.activate(SourceCountSignal(pending=3, threshold=3), below.state)
    assert len(reached.actions) == 1
    assert reached.actions[0].pending == 3
    assert reached.actions[0].threshold == 3


def test_source_count_trigger_stays_disabled_without_a_threshold() -> None:
    trigger = SourceCountTrigger()

    transition = trigger.activate(
        SourceCountSignal(pending=100, threshold=None),
        trigger.initial_state(),
    )

    assert not transition.actions
