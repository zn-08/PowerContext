# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""PDF retrieval must retain evidence for separate parts of a question."""

import asyncio

from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    GetMemoryEntryRequest,
    RememberMemoryRequest,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft
from powercontext.sources.pdf_pages import search_pdf_pages


def test_pdf_search_retains_focused_evidence_among_broad_matches(tmp_path) -> None:
    async def scenario() -> None:
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'pages.db'}"))
        focused_pages = (
            "Income statement. Income before tax 120. Income after tax 90. Net income 90.",
            "Inventory valuation. Inventory at opening 70. Inventory at closing 80. Inventory reserves 5.",
        )
        broad_pages = tuple(
            f"Business review {number}. Income and cash affect depreciation, inventory and revenue. "
            "We review income, cash, depreciation, inventory and revenue in regular management meetings."
            for number in range(40)
        )
        async with open_builtin_runtime(config) as runtime:
            scope = await runtime.scopes.create(
                ScopeDraft(title="Report", summary="Financial evidence", idempotency_key="report")
            )
            await runtime.memory.for_scope(scope.scope_id).remember(
                RememberMemoryRequest(
                    entries=tuple(
                        MemoryEntryInput(kind="source-evidence", text=text) for text in (*focused_pages, *broad_pages)
                    )
                )
            )
        async with open_builtin_runtime(config) as runtime:
            memory = runtime.memory.for_scope(scope.scope_id)
            question = "What are income, cash, depreciation, inventory and revenue?"
            citations = await search_pdf_pages(memory, question, limit=20)
            texts = {(await memory.get(GetMemoryEntryRequest(citation=citation))).entry.text for citation in citations}
            assert set(focused_pages) <= texts
            assert len(citations) == len({citation.entry_id for citation in citations}) == 20
            assert await search_pdf_pages(memory, question, limit=1) == citations[:1]
            assert await search_pdf_pages(memory, question, limit=0) == ()
            assert await search_pdf_pages(memory, " ", limit=20) == ()

    asyncio.run(scenario())
